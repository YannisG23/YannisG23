"""Boucle vocale : seule propriétaire du micro.

Elle gère l'activation (le nom de l'assistant dit naturellement, ou « Hey Jarvis »), le mode
conversation, le bouton « parler », la prise de parole pendant qu'il répond et les écoutes
demandées par d'autres (confirmations).
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from .wakename import NameSpotter

SAMPLE_RATE = 16000
MIN_PHRASE_SECONDS = 0.35  # plus court : un bruit, pas une phrase


@dataclass
class _ListenRequest:
    timeout: float
    done: threading.Event = field(default_factory=threading.Event)
    text: str = ""


class VoiceLoop:
    def __init__(self, core: Any, listener: Any) -> None:
        self.core = core
        self.listener = listener
        config = core.config
        self.spotter = NameSpotter(config.assistant_name, config.name_aliases) if config.wake_by_name else None
        self._requests: queue.Queue[_ListenRequest] = queue.Queue()
        self._push_to_talk = threading.Event()
        self._running = False

    def rename(self, name: str, aliases: list[str]) -> None:
        """Le nom a changé : c'est maintenant lui qui réveille l'assistant."""
        if self.spotter:
            self.spotter = NameSpotter(name, aliases)
        self.listener.name = name  # aide Whisper à bien l'écrire

    @property
    def activation_hint(self) -> str:
        name = self.core.config.assistant_name
        if self.spotter:
            return f"Appelle-moi par mon nom : « {name}, … »"
        if self.listener.has_wake_word:
            return "Dis « Hey Jarvis » pour me parler"
        return "Clique sur le micro pour me parler"

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="assistant-voice").start()

    def stop(self) -> None:
        self._running = False

    def push_to_talk(self) -> None:
        self._push_to_talk.set()

    def listen_once(self, timeout: float = 8.0) -> str:
        """Écoute une réponse (appel bloquant, depuis n'importe quel thread)."""
        request = _ListenRequest(timeout)
        self._requests.put(request)
        request.done.wait(timeout + 30)
        return request.text

    # ------------------------------------------------------------------ écoute

    def _listen(self, start_timeout: float) -> str:
        previous = self.core.state
        self.core.set_state("listening")
        try:
            return self.listener.listen(start_timeout=start_timeout)
        finally:
            self.core.set_state(previous if previous != "listening" else "idle")

    def _overheard(self, max_seconds: float) -> tuple[bool, str]:
        """Écoute ce qui se dit ; renvoie (on m'a appelé, demande éventuelle)."""
        audio = self.listener.record_utterance(start_timeout=0.3, max_seconds=max_seconds)
        if audio is None or len(audio) < MIN_PHRASE_SECONDS * SAMPLE_RATE:
            return False, ""
        heard = self.listener.transcribe(audio, fast=True)
        found, command = self.spotter.find(heard)
        if not found:
            return False, ""
        # Nom entendu : transcription soignée de la demande.
        precise = self.listener.transcribe(audio)
        found_again, precise_command = self.spotter.find(precise)
        return True, precise_command if found_again else command

    def _called(self) -> tuple[bool, str]:
        """Attend brièvement un appel ; renvoie (appelé, demande déjà formulée)."""
        if self._push_to_talk.is_set():
            self._push_to_talk.clear()
            return True, ""
        if self.spotter:
            return self._overheard(max_seconds=15)
        if self.listener.has_wake_word:
            return self.listener.wait_for_wake_word(timeout=0.3), ""
        self.listener.drain()
        return False, ""

    def _interrupted(self) -> tuple[bool, str]:
        """Pendant qu'il parle ou réfléchit : l'appeler le coupe."""
        if not self.core.config.barge_in:
            self.listener.drain()  # ne pas s'entendre soi-même
            return False, ""
        if self.spotter:
            return self._overheard(max_seconds=5)
        if self.listener.has_wake_word:
            # Seuil plus exigeant, pour ne pas se déclencher sur sa propre voix.
            strict = min(0.95, self.listener.wake_threshold + 0.2)
            return self.listener.wait_for_wake_word(timeout=0.3, threshold=strict, adapt=False), ""
        self.listener.drain()
        return False, ""

    def _run(self) -> None:
        core, listener = self.core, self.listener
        while self._running:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                request = None
            if request is not None:
                core.speaker.wait(timeout=30)
                listener.flush()
                request.text = self._listen(request.timeout)
                request.done.set()
                continue

            if core.busy or core.speaker.speaking:
                called, command = self._interrupted()
                if called:
                    core.stop_speaking()
                    core.bus.publish("barge_in")
                    if command:
                        core.submit(command, "voice")
                    else:
                        self._push_to_talk.set()  # écoute dès que la réponse abandonnée est close
                continue

            if core.awaiting_follow_up:
                # Mode conversation : on peut enchaîner sans rappeler l'assistant.
                core.awaiting_follow_up = False
                self._push_to_talk.clear()
                listener.flush()
                text = self._listen(core.config.follow_up_seconds)
                if text:
                    core.submit(text, "voice")
                continue

            called, command = self._called()
            if not called:
                continue
            core.bus.publish("wake")
            if command:
                core.submit(command, "voice")
                continue
            beep()
            listener.flush()
            text = self._listen(6.0)
            if text:
                core.submit(text, "voice")


def beep() -> None:
    """Petit signal sonore à deux notes : « je t'écoute »."""
    try:
        import numpy as np
        import sounddevice as sd

        rate = 24000
        notes = []
        for freq, dur in ((740, 0.07), (988, 0.09)):
            t = np.linspace(0, dur, int(rate * dur), endpoint=False)
            notes.append(0.16 * np.sin(2 * np.pi * freq * t) * np.hanning(t.size))
        sd.play(np.concatenate(notes).astype(np.float32), samplerate=rate)
        sd.wait()
    except Exception:
        pass
