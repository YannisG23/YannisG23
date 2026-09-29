"""Le noyau de Jarvis : orchestre le cerveau, la voix, le centre de commande et les tâches de fond."""

from __future__ import annotations

import itertools
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import anthropic

from .brain import Brain, BrainError, Interrupted
from .config import Config
from .memory import Memory
from .tools import registry
from .tools import google_tools

YES = re.compile(r"\b(oui|ouais|yes|ok|okay|vas-y|go|confirme|confirmé|d'accord|carrément|bien sûr|fais-le|valide)\b", re.I)
NO = re.compile(r"\b(non|no|annule|stop|surtout pas|pas du tout|laisse tomber)\b", re.I)
RESET = re.compile(r"nouvelle conversation|on repart de zéro|change(ons)? de sujet complètement", re.I)

# Événements conservés pour les clients du centre de commande qui se connectent en cours de route.
_HISTORY_KINDS = {"user_message", "assistant_message", "tool_start", "tool_end", "notification", "error",
                  "consolidated", "confirm_request", "confirm_resolved"}


def is_yes(answer: str) -> bool:
    return bool(YES.search(answer)) and not NO.search(answer)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.history: deque[dict] = deque(maxlen=400)

    def publish(self, kind: str, data: dict | None = None) -> None:
        event = {"id": next(self._ids), "type": kind, "data": data or {}, "ts": datetime.now().isoformat(timespec="seconds")}
        with self._lock:
            if kind in _HISTORY_KINDS:
                self.history.append(event)
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(event)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


@dataclass
class Job:
    kind: str  # "ask" ou "consolidate"
    text: str = ""
    source: str = "text"  # "voice", "dashboard", "text", "system"
    done: threading.Event = field(default_factory=threading.Event)
    result: str = ""
    ok: bool = True  # False si la réponse a échoué (Claude injoignable, erreur…)


@dataclass
class PendingConfirm:
    id: int
    action: str
    done: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None


class Core:
    def __init__(self, config: Config, memory: Memory | None = None, client: Any | None = None,
                 speaker: Any | None = None) -> None:
        from .voice.speak import SilentSpeaker

        self.config = config
        self.bus = EventBus()
        self.memory = memory or Memory(config.memory_db)
        self.memory.on_change = lambda kind: self.bus.publish("memory_changed", {"kind": kind})
        self.speaker = speaker or SilentSpeaker()
        if hasattr(self.speaker, "on_state"):
            self.speaker.on_state = self._on_speaking
        if hasattr(self.speaker, "on_play"):
            # Sous-titre synchronisé : la phrase s'affiche quand elle commence à être dite.
            self.speaker.on_play = lambda text: self.bus.publish("caption", {"text": text})
        if config.uses_subscription and client is None:
            from .brain_subscription import SubscriptionBrain

            self.brain: Brain = SubscriptionBrain(config, self.memory, registry, confirm=self.confirm,
                                                  notify=self.notify, on_event=self.bus.publish)
        else:
            self.brain = Brain(config, self.memory, registry, client=client, confirm=self.confirm,
                               notify=self.notify, on_event=self.bus.publish)
        self.voice = None  # VoiceLoop, branchée par l'application en mode vocal
        self._name = config.assistant_name
        self.state = "idle"
        self.awaiting_follow_up = False
        self.last_activity = time.monotonic()
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._busy = False
        self._confirm_ids = itertools.count(1)
        self._confirms: dict[int, PendingConfirm] = {}
        self._reminded: set[str] = set()
        self._running = True
        threading.Thread(target=self._worker, daemon=True, name="jarvis-brain").start()
        threading.Thread(target=self._watcher, daemon=True, name="jarvis-watcher").start()
        threading.Thread(target=self._levels, daemon=True, name="jarvis-levels").start()

    # ------------------------------------------------------------------ état

    @property
    def busy(self) -> bool:
        return self._busy or not self._jobs.empty()

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.bus.publish("state", {"state": state})

    def _on_speaking(self, speaking: bool) -> None:
        if speaking:
            self.set_state("speaking")
        elif not self._busy:
            self.set_state("idle")

    # --------------------------------------------------------------- requêtes

    def submit(self, text: str, source: str = "text") -> Job:
        job = Job("ask", text.strip(), source)
        self._jobs.put(job)
        return job

    def ask(self, text: str, source: str = "text", timeout: float | None = None) -> str:
        """Version bloquante de submit()."""
        job = self.submit(text, source)
        job.done.wait(timeout)
        return job.result

    def briefing(self, source: str = "dashboard") -> Job:
        return self.submit("Fais-moi mon briefing : la date, la météo, mon agenda du jour, "
                           "mes e-mails importants non lus et mes tâches en cours.", source)

    def new_conversation(self) -> Job:
        job = Job("consolidate", source="dashboard")
        self._jobs.put(job)
        return job

    def say(self, text: str) -> None:
        self.speaker.say(text)

    def stop_speaking(self) -> None:
        """Coupe la parole et abandonne la réponse en cours."""
        if self._busy:
            self.brain.interrupt()
        self.speaker.stop()
        self.bus.publish("stopped")

    def notify(self, text: str) -> None:
        self.bus.publish("notification", {"text": text})
        self.say(text)

    # ---------------------------------------------------------------- travail

    def _worker(self) -> None:
        while self._running:
            job = self._jobs.get()
            self._busy = True
            self.last_activity = time.monotonic()
            try:
                if job.kind == "consolidate":
                    self._consolidate()
                    job.result = "Nouvelle conversation."
                elif RESET.search(job.text):
                    self.bus.publish("user_message", {"text": job.text, "source": job.source})
                    self._consolidate()
                    job.result = "C'est noté, on repart de zéro. Je garde bien sûr tout en mémoire."
                    self._reply(job.result)
                else:
                    job.result = self._answer(job)
            finally:
                self._apply_rename()
                self._busy = False
                self.last_activity = time.monotonic()
                self.awaiting_follow_up = job.source == "voice"
                if not self.speaker.speaking:
                    self.set_state("idle")
                job.done.set()

    def _apply_rename(self) -> None:
        """Après un changement de nom à la voix : réveil, personnalité et interface suivent."""
        name = self.config.assistant_name
        if name == self._name:
            return
        self._name = name
        if self.voice is not None:
            self.voice.rename(name, self.config.name_aliases)
        # La personnalité est figée pour une conversation : on en ouvre une nouvelle avec le nouveau nom.
        if self.brain.turns:
            self._consolidate()
        else:
            self.brain.reset()
        self.bus.publish("renamed", {"name": name})

    def _reply(self, text: str) -> None:
        self.bus.publish("assistant_message", {"text": text})
        self.say(text)

    def _answer(self, job: Job) -> str:
        self.set_state("thinking")
        if job.source != "system":  # une consigne interne n'apparaît pas comme si tu l'avais dite
            self.bus.publish("user_message", {"text": job.text, "source": job.source})
        turn = next(self._confirm_ids)
        try:
            reply = self.brain.ask(
                job.text,
                on_sentence=self.say,
                on_delta=lambda delta: self.bus.publish("assistant_delta", {"turn": turn, "text": delta}),
            )
        except Interrupted:
            reply = "(interrompu)"
        except Exception as exc:
            job.ok = False
            if isinstance(exc, BrainError):
                reply = str(exc)
            elif isinstance(exc, anthropic.AuthenticationError):
                reply = "Ma clé d'API Anthropic est invalide. Vérifie ANTHROPIC_API_KEY dans le fichier point env."
            elif isinstance(exc, anthropic.RateLimitError):
                reply = "Je suis limité en nombre de requêtes pour le moment. Réessaie dans une minute."
            elif isinstance(exc, anthropic.APIConnectionError):
                reply = "Je n'arrive pas à joindre mes serveurs. Vérifie la connexion internet."
            elif isinstance(exc, anthropic.APIStatusError):
                reply = f"Petit souci côté serveur, erreur {exc.status_code}. Réessaie dans un instant."
                self.bus.publish("error", {"message": str(exc)})
            else:
                reply = "Quelque chose s'est mal passé de mon côté."
                self.bus.publish("error", {"message": f"{type(exc).__name__}: {exc}"})
            self.say(reply)
        self.bus.publish("assistant_message", {"turn": turn, "text": reply})
        if self.brain.needs_consolidation:
            self._consolidate()
        return reply

    def _consolidate(self) -> None:
        if self.brain.turns == 0:
            return
        self.set_state("thinking")
        self.bus.publish("consolidating")
        self.brain.consolidate()

    # --------------------------------------------------------- confirmations

    def confirm(self, action: str) -> bool:
        """Demande l'accord de l'utilisateur (à la voix et dans le centre de commande)."""
        pending = PendingConfirm(next(self._confirm_ids), action)
        self._confirms[pending.id] = pending
        question = f"Je dois {action}. Tu confirmes ?"
        self.bus.publish("confirm_request", {"id": pending.id, "action": action, "question": question})
        self.say(question)
        if self.voice is not None:
            def by_voice() -> None:
                self.speaker.wait(timeout=30)
                answer = self.voice.listen_once(timeout=8)
                if answer and not pending.done.is_set():
                    self.bus.publish("user_message", {"text": answer, "source": "voice"})
                    self.resolve_confirm(pending.id, is_yes(answer))

            threading.Thread(target=by_voice, daemon=True).start()
        pending.done.wait(timeout=60)
        self._confirms.pop(pending.id, None)
        approved = bool(pending.approved)
        if pending.approved is None:
            self.bus.publish("confirm_resolved", {"id": pending.id, "approved": False, "timeout": True})
        return approved

    def resolve_confirm(self, confirm_id: int, approved: bool) -> bool:
        pending = self._confirms.get(confirm_id)
        if pending is None or pending.done.is_set():
            return False
        pending.approved = approved
        pending.done.set()
        self.bus.publish("confirm_resolved", {"id": confirm_id, "approved": approved})
        return True

    # ----------------------------------------------------------- tâches de fond

    def _levels(self) -> None:
        """Niveaux sonores (micro et voix) ~14 fois par seconde, pour animer le centre de commande."""
        last = (0.0, 0.0)
        while self._running:
            time.sleep(0.07)
            mic = min(1.0, self.voice.listener.level / 3000.0) if self.voice else 0.0
            out = float(getattr(self.speaker, "level", 0.0) or 0.0)
            current = (round(mic, 2), round(out, 2))
            if current != last and (max(current) > 0.01 or max(last) > 0.01):
                self.bus.publish("levels", {"mic": current[0], "out": current[1]})
            last = current

    def _watcher(self) -> None:
        last_calendar_check = 0.0
        while self._running:
            time.sleep(20)
            idle_for = time.monotonic() - self.last_activity
            if (self.brain.turns and not self.busy and not self.speaker.speaking
                    and idle_for > self.config.idle_minutes * 60):
                self._jobs.put(Job("consolidate", source="system"))
                self.last_activity = time.monotonic()
            if (self.config.event_reminder_minutes > 0 and google_tools.is_connected(self.config)
                    and time.monotonic() - last_calendar_check > 120):
                last_calendar_check = time.monotonic()
                try:
                    self._check_upcoming_events()
                except Exception as exc:
                    self.bus.publish("error", {"message": f"Agenda : {exc}"})

    def _check_upcoming_events(self) -> None:
        now = datetime.now().astimezone()
        window = timedelta(minutes=self.config.event_reminder_minutes)
        for event in google_tools.list_events(self.brain.ctx, days=1):
            if event["all_day"] or event["id"] in self._reminded:
                continue
            start = datetime.fromisoformat(event["start"])
            if now <= start <= now + window:
                self._reminded.add(event["id"])
                minutes = max(1, round((start - now).total_seconds() / 60))
                where = f", {event['location']}" if event["location"] else ""
                self.notify(f"{event['title']} commence dans {minutes} minutes{where}.")

    def shutdown(self) -> None:
        """Consolide la conversation en cours avant de quitter."""
        self._running = False
        if self.brain.turns:
            try:
                self.brain.consolidate()
            except Exception:
                pass
        self.brain.close()
        self.memory.close()
