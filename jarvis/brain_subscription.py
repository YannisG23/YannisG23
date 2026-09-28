"""Cerveau « abonnement » : Claude via Claude Code et ton compte Claude (Pro ou Max).

Pas de clé API ni de facturation à l'usage : les échanges comptent dans les limites de
ton abonnement. Tous les outils de Jarvis sont fournis à Claude Code sous la forme d'un
serveur MCP interne ; la recherche et la lecture web utilisent les outils de Claude Code.

Connexion (une seule fois) : python -m jarvis --login
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable

from .brain import CONSOLIDATION_SCHEMA, Brain, BrainError, Interrupted, SentenceSplitter, spoken_timestamp

SERVER = "jarvis"
WEB_TOOLS = ["WebSearch", "WebFetch"]


def find_claude_cli() -> str | None:
    """Le Claude Code livré avec claude-agent-sdk, sinon celui installé sur le système."""
    try:
        import claude_agent_sdk
    except ImportError:
        return shutil.which("claude")
    name = "claude.exe" if platform.system() == "Windows" else "claude"
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    return str(bundled) if bundled.is_file() else shutil.which("claude")


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(var, None)
    return env


def login() -> int:
    """Connecte ton compte Claude (Pro/Max) : ouvre le navigateur pour t'identifier."""
    cli = find_claude_cli()
    if not cli:
        print("Claude Code introuvable : relance l'installation (install.bat).")
        return 1
    try:
        if auth_status().get("loggedIn"):
            print("Ton compte Claude est déjà connecté.")
            return 0
    except Exception:
        pass
    print("Connexion à ton compte Claude : une page va s'ouvrir dans ton navigateur.")
    return subprocess.call([cli, "auth", "login", "--claudeai"], env=_cli_env())


def auth_status() -> dict:
    """État de la connexion au compte Claude (loggedIn, authMethod...)."""
    cli = find_claude_cli()
    if not cli:
        raise RuntimeError("Claude Code introuvable")
    out = subprocess.run([cli, "auth", "status", "--json"], capture_output=True, text=True,
                         timeout=30, env=_cli_env())
    return json.loads(out.stdout or "{}")


def _to_mcp(content: Any) -> list[dict[str, Any]]:
    """Convertit le résultat d'un outil Jarvis en contenu MCP."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks = []
    for block in content:
        if block.get("type") == "image":
            source = block["source"]
            blocks.append({"type": "image", "data": source["data"], "mimeType": source["media_type"]})
        else:
            blocks.append({"type": "text", "text": block.get("text", "")})
    return blocks


def _limit_message(info: Any) -> str:
    resets = getattr(info, "resets_at", None)
    when = ""
    if isinstance(resets, (int, float)):
        when = f" Elle se réinitialise à {datetime.fromtimestamp(resets):%H:%M}."
    return f"Tu as atteint la limite d'utilisation de ton abonnement Claude.{when}"


class _NoApiClient:
    """Le cerveau abonnement n'utilise jamais l'API facturée à l'usage."""


class SubscriptionBrain(Brain):
    def __init__(self, config: Any, memory: Any, registry: Any, sdk: Any = None,
                 confirm: Callable[[str], bool] | None = None,
                 notify: Callable[[str], None] | None = None,
                 on_event: Callable[[str, dict], None] | None = None) -> None:
        if sdk is None:
            import claude_agent_sdk as sdk
        self.sdk = sdk
        # Sinon Claude Code utiliserait la clé API (facturée à l'usage) au lieu de l'abonnement.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            os.environ.pop(var, None)
        self._client: Any = None
        self._turn_count = 0
        self._ids = itertools.count(1)
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="jarvis-claude-code").start()
        super().__init__(config, memory, registry, client=_NoApiClient(), confirm=confirm,
                         notify=notify, on_event=on_event)
        self._tool_handlers = {name: self._make_handler(name) for name in sorted(registry.tools)}
        self._server = sdk.create_sdk_mcp_server(
            name=SERVER,
            version="1.0.0",
            tools=[
                sdk.tool(tool.name, tool.description, tool.input_schema)(self._tool_handlers[tool.name])
                for tool in (registry.tools[n] for n in sorted(registry.tools))
            ],
        )

    # ------------------------------------------------------------------ plomberie

    def _run(self, coro: Any, timeout: float | None = None) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def _build_tools(self) -> list[dict[str, Any]]:
        return []  # les outils passent par le serveur MCP

    @property
    def turns(self) -> int:
        return self._turn_count

    def _new_session(self) -> None:
        if getattr(self, "_client", None) is not None:
            try:
                self._run(self._client.disconnect(), timeout=15)
            except Exception:
                pass
            self._client = None
        self._turn_count = 0
        super()._new_session()

    def _options(self) -> Any:
        persona, profile = (block["text"] for block in self.system)
        tool_names = [f"mcp__{SERVER}__{name}" for name in sorted(self.registry.tools)]
        return self.sdk.ClaudeAgentOptions(
            system_prompt=(
                f"{persona}\n\n{profile}\n\n"
                "Pour la recherche et la lecture web, utilise les outils WebSearch et WebFetch."
            ),
            mcp_servers={SERVER: self._server},
            tools=WEB_TOOLS,
            allowed_tools=[*tool_names, *WEB_TOOLS],
            setting_sources=[],
            model=self.config.model,
            effort=self.config.effort,
            include_partial_messages=True,
            cwd=str(self.config.home),
            max_turns=self.config.max_tool_steps + 5,
        )

    def _make_handler(self, name: str) -> Callable[[dict], Any]:
        async def handle(args: dict) -> dict:
            block = SimpleNamespace(id=f"{name}-{next(self._ids)}", name=name, input=args)
            # Les outils sont synchrones et peuvent attendre une confirmation : hors de la boucle.
            result = await asyncio.to_thread(self._execute, block)
            out: dict[str, Any] = {"content": _to_mcp(result["content"])}
            if result.get("is_error"):
                out["is_error"] = True
            return out

        return handle

    # --------------------------------------------------------------- conversation

    def ask(self, text: str, on_sentence: Callable[[str], None] | None = None,
            on_delta: Callable[[str], None] | None = None) -> str:
        self._cancel = False
        prompt = f"[{spoken_timestamp(datetime.now())}] {text}{self._recall_block(text)}"
        try:
            reply = self._run(self._turn(prompt, on_sentence, on_delta))
        except BrainError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if name == "CLINotFoundError":
                raise BrainError("Je ne trouve pas Claude Code. Relance l'installation avec install point bat.") from exc
            if name in {"ProcessError", "CLIConnectionError"}:
                self._client = None  # on repartira sur une connexion neuve
                raise BrainError(
                    "Je n'arrive pas à joindre Claude. Vérifie ta connexion internet, "
                    "ou reconnecte ton compte avec python -m jarvis --login."
                ) from exc
            raise
        self._turn_count += 1
        self.memory.log("user", text, self.session_id)
        self.memory.log("assistant", reply, self.session_id)
        return reply

    async def _turn(self, prompt: str, on_sentence, on_delta) -> str:
        sdk = self.sdk
        if self._client is None:
            self._client = sdk.ClaudeSDKClient(options=self._options())
            await self._client.connect()
        await self._client.query(prompt)

        splitter = SentenceSplitter()
        streamed: list[str] = []
        final_texts: list[str] = []
        limit_error: str | None = None

        def speak(sentence: str) -> None:
            if sentence and on_sentence and not self._cancel:
                on_sentence(sentence)

        async for message in self._client.receive_response():
            if isinstance(message, sdk.StreamEvent):
                if message.parent_tool_use_id is not None:
                    continue  # activité interne d'un sous-agent
                event = message.event
                if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                    delta = event["delta"]["text"]
                    streamed.append(delta)
                    if on_delta and not self._cancel:
                        on_delta(delta)
                    for sentence in splitter.feed(delta):
                        speak(sentence)
                elif event.get("type") == "message_stop":
                    speak(splitter.flush())
            elif isinstance(message, sdk.AssistantMessage) and message.parent_tool_use_id is None:
                final_texts += [b.text for b in message.content if isinstance(b, sdk.TextBlock)]
            elif isinstance(message, sdk.RateLimitEvent):
                if getattr(message.rate_limit_info, "status", "") == "rejected":
                    # On lit la réponse jusqu'au bout avant de signaler l'erreur, sinon ses
                    # derniers messages arriveraient au tour suivant.
                    limit_error = _limit_message(message.rate_limit_info)
            elif isinstance(message, sdk.ResultMessage):
                usage = message.usage or {}
                self.context_tokens = sum(
                    int(usage.get(k) or 0)
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
                )
                if self._cancel:
                    raise Interrupted("Interrompu.")
                if limit_error:
                    raise BrainError(limit_error)
                if message.is_error:
                    detail = message.result or ", ".join(message.errors or []) or message.subtype
                    if message.api_error_status == 429 or "limit" in str(detail).lower():
                        raise BrainError("Tu as atteint la limite d'utilisation de ton abonnement Claude pour le moment.")
                    raise BrainError(f"Claude a rencontré un problème : {detail}")

        speak(splitter.flush())
        if not streamed and final_texts:
            # Pas de flux partiel reçu : on dit le texte complet d'un coup.
            for text in final_texts:
                speak(text)
                if on_delta:
                    on_delta(text)
        return " ".join(t.strip() for t in final_texts if t.strip()) or "".join(streamed).strip()

    def interrupt(self) -> None:
        self._cancel = True
        if self._client is not None:
            asyncio.run_coroutine_threadsafe(self._client.interrupt(), self._loop)

    # ------------------------------------------------------------ consolidation

    def _summarize(self, transcript: str, known: str) -> dict | None:
        return self._run(self._summarize_async(self._consolidation_prompt(transcript, known)), timeout=300)

    async def _summarize_async(self, prompt: str) -> dict | None:
        options = self.sdk.ClaudeAgentOptions(
            system_prompt="Tu résumes des conversations pour la mémoire long terme d'un assistant personnel.",
            tools=[],
            setting_sources=[],
            model=self.config.model,
            effort="low",
            output_format={"type": "json_schema", "schema": CONSOLIDATION_SCHEMA},
            cwd=str(self.config.home),
            max_turns=3,
        )
        data = None
        async for message in self.sdk.query(prompt=prompt, options=options):
            if isinstance(message, self.sdk.ResultMessage) and not message.is_error:
                data = message.structured_output or (json.loads(message.result) if message.result else None)
        return data

    def close(self) -> None:
        if self._client is not None:
            try:
                self._run(self._client.disconnect(), timeout=10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
