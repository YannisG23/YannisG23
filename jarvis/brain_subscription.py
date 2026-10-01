"""Cerveau « abonnement » : Claude via Claude Code et ton compte Claude (Pro ou Max).

Pas de clé API ni de facturation à l'usage : les échanges comptent dans les limites de
ton abonnement. Tous les outils de Jarvis sont fournis à Claude Code sous la forme d'un
serveur MCP interne ; la recherche et la lecture web utilisent les outils de Claude Code.

Connexion (une seule fois) : python -m jarvis --login
"""

from __future__ import annotations

import asyncio
import collections
import itertools
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable

from . import usage
from .brain import CONSOLIDATION_SCHEMA, Brain, BrainError, Interrupted, SentenceSplitter, spoken_timestamp

SERVER = "jarvis"
WEB_TOOLS = ["WebSearch", "WebFetch"]
MISSING_CLI = ("Il me manque Claude Code pour Windows. Ferme-moi, double-clique sur connexion point bat : "
               "il l'installe et connecte ton compte. Ensuite relance-moi.")


def _claude_candidates() -> list[Path]:
    windows = platform.system() == "Windows"
    name = "claude.exe" if windows else "claude"
    candidates: list[Path] = []
    try:
        import claude_agent_sdk

        candidates.append(Path(claude_agent_sdk.__file__).parent / "_bundled" / name)
    except ImportError:
        pass
    # Installation officielle (irm https://claude.ai/install.ps1 | iex, ou curl … | bash).
    candidates.append(Path.home() / ".local" / "bin" / name)
    if windows:
        # Certaines versions npm embarquent aussi un vrai claude.exe dans leurs dossiers.
        npm = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@anthropic-ai"
        if npm.is_dir():
            candidates += sorted(npm.rglob("claude.exe"))
    found = shutil.which("claude")
    if found:
        candidates.append(Path(found))
    return candidates


def find_claude_cli() -> str | None:
    """Un Claude Code exécutable directement.

    Sous Windows, seul un vrai claude.exe convient : le kit de Claude refuse, par sécurité,
    le script claude.cmd qu'installe npm.
    """
    windows = platform.system() == "Windows"
    for path in _claude_candidates():
        if path.is_file() and (not windows or path.suffix.lower() == ".exe"):
            return str(path)
    return None


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(var, None)
    return env


def login() -> int:
    """Connecte ton compte Claude (Pro/Max) : ouvre le navigateur pour t'identifier."""
    cli = find_claude_cli()
    if not cli:
        print("Claude Code (claude.exe) introuvable : double-clique sur connexion.bat, il l'installe.")
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


class LimitReached(BrainError):
    """Claude est indisponible : limite d'usage de l'abonnement. resets_at : heure de retour (epoch), si connue."""

    def __init__(self, message: str, resets_at: float | None = None) -> None:
        super().__init__(message)
        self.resets_at = resets_at


# Sans heure de réinitialisation connue, on retente Claude après ce délai.
RELAY_RETRY_SECONDS = 15 * 60
RELAY_TIMEOUT = 150
RELAY_HISTORY = 6


def _limit_message(info: Any) -> str:
    resets = getattr(info, "resets_at", None)
    when = ""
    if isinstance(resets, (int, float)):
        when = f" Elle se réinitialise à {datetime.fromtimestamp(resets):%H:%M}."
    return f"Tu as atteint la limite d'utilisation de ton abonnement Claude.{when}"


class _NoApiClient:
    """Le cerveau abonnement n'utilise jamais l'API facturée à l'usage."""


SUBSCRIPTION_DEFAULT_MODEL = "sonnet"
ECONOMY_MODEL = "haiku"


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
        self._economy = False
        # Relais Codex : heure (epoch) où l'on retente Claude, derniers échanges, échanges faits par ChatGPT.
        self._relay_until: float | None = None
        self._recent: collections.deque[tuple[str, str]] = collections.deque(maxlen=RELAY_HISTORY)
        self._relay_notes: list[tuple[str, str]] = []
        # Dernières lignes d'erreur de Claude Code : indispensables pour comprendre un échec.
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        # Sans modèle imposé dans .env : Sonnet, rapide et malin, idéal pour une conversation à la voix
        # (et plus économe en quota). JARVIS_MODEL=opus pour le plus puissant.
        self._model: str | None = config.model if config.model_is_explicit else SUBSCRIPTION_DEFAULT_MODEL
        self._ids = itertools.count(1)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="jarvis-claude-code")
        self._thread.start()
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

    def _drop_client(self) -> None:
        """Ferme la connexion à Claude Code (sinon son processus reste orphelin, surtout sous Windows)."""
        client, self._client = self._client, None
        if client is not None:
            try:
                self._run(client.disconnect(), timeout=5)
            except Exception:
                pass

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
        # Passé par fichier : avec une mémoire bien remplie, la ligne de commande dépasserait
        # la longueur maximale autorisée par Windows.
        prompt_file = self.config.home / "system_prompt.txt"
        prompt_file.write_text(
            f"{persona}\n\n{profile}\n\n"
            "Pour la recherche et la lecture web, utilise les outils WebSearch et WebFetch.",
            encoding="utf-8",
        )
        return self.sdk.ClaudeAgentOptions(
            system_prompt={"type": "file", "path": str(prompt_file)},
            mcp_servers={SERVER: self._server},
            tools=WEB_TOOLS,
            allowed_tools=[*tool_names, *WEB_TOOLS],
            setting_sources=[],
            model=self._model,
            effort=self.config.effort,
            include_partial_messages=True,
            cwd=str(self.config.home),
            max_turns=self.config.max_tool_steps + 5,
            stderr=self._stderr.append,
            cli_path=find_claude_cli(),
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
        if self._relay_until is not None and time.time() < self._relay_until:
            relayed = self._relay(text, on_sentence, on_delta, announce=False)
            if relayed is not None:
                return relayed
        try:
            reply = self._ask_claude(text, on_sentence, on_delta)
        except LimitReached as limit:
            relayed = self._relay(text, on_sentence, on_delta, announce=True, resets_at=limit.resets_at)
            if relayed is None:
                raise
            return relayed
        self._balance()
        if self.active_brain != "claude":
            self._set_brain("claude")
        self._relay_until = None
        self._recent.append((text, reply))
        return reply

    def _balance(self) -> None:
        """Quota Claude tendu → Haiku (bien moins gourmand) jusqu'à la réinitialisation ; puis retour à Sonnet."""
        if usage.current is None or self.config.model_is_explicit:
            return
        tight = usage.current.claude_tight
        if tight and not self._economy:
            self._economy, self._model = True, ECONOMY_MODEL
            self._drop_client()  # le prochain tour repart avec le modèle économe
            self.emit("usage", {"economy": True})
            self.emit("error", {"message": "Quota Claude bientôt atteint : je passe en mode économie (Haiku) "
                                           "jusqu'à sa réinitialisation."})
        elif not tight and self._economy:
            self._economy, self._model = False, SUBSCRIPTION_DEFAULT_MODEL
            self._drop_client()
            self.emit("usage", {"economy": False})

    def _ask_claude(self, text: str, on_sentence, on_delta) -> str:
        notes = ""
        if self._relay_notes:
            notes = ("\n\n<relais_chatgpt>Pendant que tu étais indisponible, ChatGPT a répondu à ces échanges :\n"
                     + "\n".join(f"- {u} → {r}" for u, r in self._relay_notes) + "\n</relais_chatgpt>")
        prompt = f"[{spoken_timestamp(datetime.now())}] {text}{self._recall_block(text)}{notes}"
        reply = ""
        for attempt in (1, 2):
            try:
                reply = self._run(self._turn(prompt, on_sentence, on_delta))
                break
            except BrainError:
                raise
            except Exception as exc:
                name = type(exc).__name__
                if name == "CLINotFoundError" or "batch script" in str(exc):
                    self._report(exc)
                    raise BrainError(MISSING_CLI) from exc
                if name not in {"ProcessError", "CLIConnectionError"}:
                    raise
                self._drop_client()  # on repartira sur une connexion neuve
                self._report(exc)
                if attempt == 2:
                    diagnosis = self._diagnose()
                    if "limite" in diagnosis:
                        raise LimitReached(diagnosis) from exc
                    raise BrainError(diagnosis) from exc
                # Deuxième essai : connexion neuve et modèle par défaut de l'abonnement
                # (le modèle demandé n'est peut-être pas inclus dans ton offre).
                self._model = None
        self._relay_notes = []
        self._turn_count += 1
        if self.log_turns:
            self.memory.log("user", text, self.session_id)
            self.memory.log("assistant", reply, self.session_id)
        return reply

    # ------------------------------------------------------------- relais ChatGPT

    def _set_brain(self, name: str, until: float | None = None) -> None:
        if name != self.active_brain:
            self.active_brain = name
            self.emit("brain", {"active": name, "until": until})

    def _relay_prompt(self, text: str) -> str:
        history = list(self._recent) + self._relay_notes
        past = "\n".join(f"{self.config.user_name} : {u}\nToi : {r}" for u, r in history[-RELAY_HISTORY:])
        return (
            f"Tu es {self.config.assistant_name}, l'assistant vocal personnel de {self.config.user_name}. "
            f"Tu remplaces temporairement Claude, indisponible. Réponds en français, à l'oral : une à trois "
            f"phrases, sans markdown, sans liste ni émoji. Tu n'as accès ni à son PC ni à sa mémoire : si la "
            f"demande en a besoin, dis-le simplement et propose d'attendre le retour de Claude.\n\n"
            + (f"Derniers échanges :\n{past}\n\n" if past else "")
            + f"Demande de {self.config.user_name} : {text}")

    def _relay(self, text: str, on_sentence, on_delta, announce: bool,
               resets_at: float | None = None) -> str | None:
        """Répond via Codex (abonnement ChatGPT). None si Codex ne peut pas : comportement habituel."""
        from .tools import codex_tools

        if codex_tools.find_codex() is None:
            return None
        if announce:
            self._relay_until = resets_at if resets_at and resets_at > time.time() else time.time() + RELAY_RETRY_SECONDS
        try:
            answer = codex_tools.exec_codex(self._relay_prompt(text), write=False, timeout=RELAY_TIMEOUT)
        except codex_tools.CodexError as exc:
            self.emit("error", {"message": f"Relais ChatGPT impossible : {exc}"})
            self._relay_until = None
            return None
        self._set_brain("chatgpt", self._relay_until)
        back = ""
        if announce:
            if self._relay_until:
                back = f" Claude revient vers {datetime.fromtimestamp(self._relay_until):%H:%M}."
            intro = f"Claude a atteint sa limite : je passe sur ChatGPT le temps qu'il revienne.{back}"
        else:
            intro = ""
        full = f"{intro} {answer}".strip()
        for sentence in ([intro] if intro else []) + [answer]:
            if on_delta and not self._cancel:
                on_delta(sentence + " ")
            splitter = SentenceSplitter()
            for part in [*splitter.feed(sentence), splitter.flush()]:
                if part and on_sentence and not self._cancel:
                    on_sentence(part)
        self._relay_notes.append((text, answer))
        self._relay_notes = self._relay_notes[-RELAY_HISTORY:]
        self._recent.append((text, answer))
        if self.log_turns:
            self.memory.log("user", text, self.session_id)
            self.memory.log("assistant", full, self.session_id)
        return full

    async def _turn(self, prompt: str, on_sentence, on_delta) -> str:
        sdk = self.sdk
        if self._client is None:
            if find_claude_cli() is None:
                raise BrainError(MISSING_CLI)
            self._client = sdk.ClaudeSDKClient(options=self._options())
            await self._client.connect()
        await self._client.query(prompt)

        splitter = SentenceSplitter()
        streamed: list[str] = []
        final_texts: list[str] = []
        limit_error: str | None = None
        limit_resets: float | None = None

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
                info = message.rate_limit_info
                if usage.current is not None:
                    resets_at = getattr(info, "resets_at", None)
                    usage.current.claude_status(getattr(info, "status", "") or "", getattr(info, "utilization", None),
                                                float(resets_at) if isinstance(resets_at, (int, float)) else None)
                if getattr(message.rate_limit_info, "status", "") == "rejected":
                    # On lit la réponse jusqu'au bout avant de signaler l'erreur, sinon ses
                    # derniers messages arriveraient au tour suivant.
                    limit_error = _limit_message(message.rate_limit_info)
                    resets = getattr(message.rate_limit_info, "resets_at", None)
                    limit_resets = float(resets) if isinstance(resets, (int, float)) else None
            elif isinstance(message, sdk.ResultMessage):
                tokens = message.usage or {}
                self.context_tokens = sum(
                    int(tokens.get(k) or 0)
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
                )
                if usage.current is not None:
                    usage.current.record("claude", self.context_tokens - int(tokens.get("output_tokens") or 0),
                                         int(tokens.get("output_tokens") or 0), message.total_cost_usd)
                if self._cancel:
                    raise Interrupted("Interrompu.")
                if limit_error:
                    raise LimitReached(limit_error, limit_resets)
                if message.is_error:
                    detail = message.result or ", ".join(message.errors or []) or message.subtype
                    if message.api_error_status == 429 or "limit" in str(detail).lower():
                        raise LimitReached("Tu as atteint la limite d'utilisation de ton abonnement Claude pour le moment.",
                                           limit_resets)
                    raise BrainError(f"Claude a rencontré un problème : {detail}")

        speak(splitter.flush())
        if not streamed and final_texts:
            # Pas de flux partiel reçu : on dit le texte complet d'un coup.
            for text in final_texts:
                speak(text)
                if on_delta:
                    on_delta(text)
        return " ".join(t.strip() for t in final_texts if t.strip()) or "".join(streamed).strip()

    def _report(self, exc: Exception) -> None:
        """Garde une trace détaillée de l'échec (onglet Activité et ~/.jarvis/erreurs.log)."""
        detail = f"{type(exc).__name__}: {exc}"
        stderr = getattr(exc, "stderr", None) or "\n".join(self._stderr)
        if stderr:
            detail += "\n" + str(stderr)[-2000:]
        self.emit("error", {"message": f"Détail technique : {detail}"})
        try:
            with open(self.config.home / "erreurs.log", "a", encoding="utf-8") as log:
                log.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {detail}\n\n")
        except OSError:
            pass

    def _diagnose(self) -> str:
        """Message à dire, selon ce que Claude Code a répondu."""
        text = "\n".join(self._stderr).lower()
        try:
            logged_in = auth_status().get("loggedIn", True)
        except Exception:
            logged_in = True
        if not logged_in or any(w in text for w in ("not logged", "log in", "login", "unauthorized", "401", "oauth")):
            return ("Ton compte Claude n'est pas connecté. Ferme-moi, double-clique sur connexion point bat, "
                    "puis relance-moi.")
        if any(w in text for w in ("usage limit", "rate limit", "quota")):
            return "Tu as atteint la limite de ton abonnement Claude pour le moment. Réessaie un peu plus tard."
        return ("Je n'arrive pas à joindre Claude. Vérifie ta connexion internet. Le détail de l'erreur est "
                "dans l'onglet Activité du centre de commande.")

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
            model=self._model,
            effort="low",
            output_format={"type": "json_schema", "schema": CONSOLIDATION_SCHEMA},
            cwd=str(self.config.home),
            max_turns=3,
            cli_path=find_claude_cli(),
        )
        data = None
        async for message in self.sdk.query(prompt=prompt, options=options):
            if isinstance(message, self.sdk.ResultMessage) and not message.is_error:
                data = message.structured_output or (json.loads(message.result) if message.result else None)
        return data

    def close(self) -> None:
        """Ferme Claude Code proprement, sans jamais bloquer la sortie plus de quelques secondes."""
        if self._loop.is_closed():
            return
        if self._client is not None:
            try:
                self._run(self._client.disconnect(), timeout=5)
            except Exception:
                pass
            self._client = None

        async def cancel_pending() -> None:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks, timeout=3) if tasks else None
            await self._loop.shutdown_asyncgens()

        try:
            self._run(cancel_pending(), timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)
        if not self._thread.is_alive():
            self._loop.close()
