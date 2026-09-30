"""Cerveau de conversation ChatGPT : ChatGPT parle, Claude agit.

ChatGPT (API OpenAI, petit modèle rapide) mène la conversation : il répond en moins d'une seconde
aux échanges du quotidien. Dès qu'il faut agir — PC, fichiers, e-mails, agenda, mémoire, web,
routines — il confie la tâche à Claude via l'outil « claude », qui dispose de tous les outils de
Jarvis (et de ses confirmations). Le quota Claude ne sert plus qu'aux vraies actions.

Si l'API OpenAI est indisponible (clé refusée, crédit épuisé, réseau), la demande part
directement à Claude, comme avant.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Callable

from .brain import (
    CONSOLIDATION_PROMPT,
    CONSOLIDATION_SCHEMA,
    Brain,
    BrainError,
    Interrupted,
    SentenceSplitter,
    spoken_timestamp,
)

API_URL = "https://api.openai.com/v1/chat/completions"
MAX_HISTORY = 40  # messages gardés dans la conversation ChatGPT (le reste vit dans la mémoire)
MAX_TOOL_ROUNDS = 4

GPT_RULES = """# Comment tu fonctionnes
Tu es la voix et la conversation de {name}. Tu parles à {user} à l'oral : phrases courtes et naturelles,
pas de markdown, pas de liste, pas d'émoji.

Tu n'as qu'un seul outil : « claude », ton second cerveau, qui a accès au PC de {user}, à ses fichiers,
ses e-mails, son agenda, la mémoire à long terme, internet, les routines, Codex, le son, l'écran…
- Dès que la demande exige une action, une information que tu n'as pas (météo, heure exacte d'un
  rendez-vous, contenu d'un fichier, actualité…), ou de retenir/oublier quelque chose, appelle « claude »
  avec une consigne complète et autonome (il ne voit pas cette conversation : donne le contexte utile).
- Tout ce que la description ci-dessous présente comme un outil, tu le fais faire par « claude ».
- Avant d'appeler « claude » pour quelque chose qui peut prendre du temps, dis une très courte phrase
  (« Je m'en occupe. »). Ensuite, rapporte le résultat en une ou deux phrases, sans répéter ce que tu as déjà dit.
- Pour une simple conversation, une question de culture générale ou un conseil, réponds toi-même, vite.
- Ne dis jamais « Claude » ou « ChatGPT » à {user} sauf s'il te le demande : pour lui, tu es {name}.
"""

CLAUDE_TOOL = {
    "type": "function",
    "function": {
        "name": "claude",
        "description": "Confie une action ou une recherche à Claude, qui a accès au PC, aux fichiers, e-mails, "
                       "agenda, mémoire, internet, routines et à tous les outils de l'assistant. Renvoie le "
                       "résultat en texte.",
        "parameters": {
            "type": "object",
            "properties": {
                "consigne": {
                    "type": "string",
                    "description": "Ce qu'il faut faire, formulé complètement, avec le contexte utile de la conversation.",
                },
            },
            "required": ["consigne"],
        },
    },
}


class GptUnavailable(RuntimeError):
    """L'API OpenAI ne répond pas comme prévu : on passe la main à Claude pour ce tour."""


class ConversationBrain:
    """Enveloppe le cerveau Claude (abonnement ou API) derrière une conversation ChatGPT."""

    def __init__(self, config: Any, claude: Brain, session: Any = None) -> None:
        import requests

        self.config = config
        self.claude = claude
        self.memory = claude.memory
        self.emit = claude.emit
        self.http = session or requests.Session()
        self.claude.log_turns = False  # c'est ce cerveau-ci qui journalise la conversation
        # Les résumés pour la mémoire long terme passent aussi par ChatGPT : zéro quota Claude.
        self._claude_summarize = claude._summarize
        self.claude._summarize = self._summarize
        self.history: list[dict[str, Any]] = []
        self._cancel = False
        self._response = None
        self._lock = threading.Lock()
        self.active_brain = "chatgpt"

    # ------------------------------------------------------------ compatibilité Core

    @property
    def ctx(self):
        return self.claude.ctx

    @property
    def turns(self) -> int:
        return sum(1 for m in self.history if m["role"] == "user")

    @property
    def context_tokens(self) -> int:
        return self.claude.context_tokens

    @property
    def needs_consolidation(self) -> bool:
        return self.claude.needs_consolidation or len(self.history) >= MAX_HISTORY * 3

    @property
    def session_id(self) -> str:
        return self.claude.session_id

    def consolidate(self):
        episode = self.claude.consolidate()
        self.history = []
        return episode

    def reset(self) -> None:
        self.consolidate()

    def interrupt(self) -> None:
        self._cancel = True
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        self.claude.interrupt()

    def close(self) -> None:
        self.claude.close()

    # ------------------------------------------------------------------ conversation

    def _system(self) -> str:
        persona, profile = (block["text"] for block in self.claude.system)
        rules = GPT_RULES.format(name=self.config.assistant_name, user=self.config.user_name)
        return f"{rules}\n\n{persona}\n\n{profile}"

    def _set_brain(self, name: str) -> None:
        if name != self.active_brain:
            self.active_brain = name
            self.emit("brain", {"active": name, "until": None})

    def ask(self, text: str, on_sentence: Callable[[str], None] | None = None,
            on_delta: Callable[[str], None] | None = None) -> str:
        self._cancel = False
        content = f"[{spoken_timestamp(datetime.now())}] {text}{self.claude._recall_block(text)}"
        start = len(self.history)
        self.history.append({"role": "user", "content": content})
        try:
            reply = self._run(on_sentence, on_delta)
            self._set_brain("chatgpt")
        except GptUnavailable as exc:
            del self.history[start:]
            self.emit("error", {"message": f"ChatGPT indisponible ({exc}) : Claude prend le relais."})
            self._set_brain("claude")
            reply = self.claude.ask(text, on_sentence, on_delta)
            self.history += [{"role": "user", "content": content}, {"role": "assistant", "content": reply}]
        except Exception:
            del self.history[start:]
            raise
        self.history = self.history[-MAX_HISTORY:]
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)  # ne jamais commencer par une réponse d'outil orpheline
        self.memory.log("user", text, self.session_id)
        self.memory.log("assistant", reply, self.session_id)
        return reply

    def _run(self, on_sentence, on_delta) -> str:
        spoken: list[str] = []
        for _round in range(MAX_TOOL_ROUNDS + 1):
            tools = [CLAUDE_TOOL] if _round < MAX_TOOL_ROUNDS else None
            text, calls = self._stream(tools, on_sentence, on_delta)
            if text:
                spoken.append(text)
            if not calls:
                return " ".join(spoken).strip()
            self.history.append({"role": "assistant", "content": text or None, "tool_calls": calls})
            for call in calls:
                self.history.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": self._call_claude(call)})
        return " ".join(spoken).strip()

    def _call_claude(self, call: dict[str, Any]) -> str:
        try:
            order = json.loads(call["function"]["arguments"] or "{}").get("consigne", "")
        except json.JSONDecodeError:
            order = call["function"]["arguments"]
        if not order:
            return "Consigne vide : reformule la demande."
        self.emit("tool_start", {"id": call["id"], "name": "claude", "args": {"consigne": order[:200]}})
        self._set_brain("claude")
        try:
            result = self.claude.ask(order)
            ok = True
        except Interrupted:
            raise
        except BrainError as exc:
            result, ok = f"Échec : {exc}", False
        self.emit("tool_end", {"id": call["id"], "name": "claude", "ok": ok, "summary": result[:200]})
        if self._cancel:
            raise Interrupted("Interrompu.")
        return result or "C'est fait."

    def _request(self, payload: dict[str, Any]):
        if not self.config.openai_api_key:
            raise GptUnavailable("clé OPENAI_API_KEY absente")
        try:
            response = self.http.post(
                API_URL, json=payload, stream=payload.get("stream", False), timeout=(10, 60),
                headers={"Authorization": f"Bearer {self.config.openai_api_key}"},
            )
        except Exception as exc:
            raise GptUnavailable(f"réseau : {exc}") from exc
        if response.status_code != 200:
            try:
                detail = response.json().get("error", {}).get("message", "")
            except Exception:
                detail = ""
            raise GptUnavailable(f"erreur {response.status_code} {detail}".strip())
        return response

    def _stream(self, tools, on_sentence, on_delta) -> tuple[str, list[dict[str, Any]]]:
        payload: dict[str, Any] = {
            "model": self.config.gpt_model,
            "messages": [{"role": "system", "content": self._system()}, *self.history],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        response = self._request(payload)
        self._response = response
        splitter = SentenceSplitter()
        parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}

        def say(sentence: str) -> None:
            if sentence and on_sentence and not self._cancel:
                on_sentence(sentence)

        try:
            for raw in response.iter_lines(decode_unicode=True):
                if self._cancel:
                    raise Interrupted("Interrompu.")
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if not chunk.get("choices"):
                    continue
                delta = chunk["choices"][0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    parts.append(piece)
                    if on_delta and not self._cancel:
                        on_delta(piece)
                    for sentence in splitter.feed(piece):
                        say(sentence)
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
        except Interrupted:
            raise
        except Exception as exc:
            if self._cancel:
                raise Interrupted("Interrompu.") from exc
            if not parts and not calls:
                raise GptUnavailable(f"flux interrompu : {exc}") from exc
        finally:
            self._response = None
        say(splitter.flush())
        return "".join(parts).strip(), [calls[i] for i in sorted(calls)]

    # ------------------------------------------------------------ mémoire long terme

    def _summarize(self, transcript: str, known: str) -> dict | None:
        prompt = CONSOLIDATION_PROMPT.format(user=self.config.user_name, known=known, transcript=transcript)
        payload = {
            "model": self.config.gpt_model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "memoire", "schema": CONSOLIDATION_SCHEMA}},
        }
        try:
            response = self._request(payload)
        except GptUnavailable:
            return self._claude_summarize(transcript, known)
        content = response.json()["choices"][0]["message"].get("content") or ""
        return json.loads(content) if content else None
