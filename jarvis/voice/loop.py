"""Boucle vocale : seule propriétaire du micro, elle sert le mot d'activation, le mode
conversation, le « appuyer pour parler » et les écoutes demandées par d'autres (confirmations)."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _ListenRequest:
    timeout: float
    done: threading.Event = field(default_factory=threading.Event)
    text: str = ""


class VoiceLoop:
    def __init__(self, core: Any, listener: Any) -> None:
        self.core = core
        self.listener = listener
        self._requests: queue.Queue[_ListenRequest] = queue.Queue()
        self._push_to_talk = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="jarvis-voice").start()

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

    def _listen(self, start_timeout: float) -> str:
        previous = self.core.state
        self.core.set_state("listening")
        try:
            return self.listener.listen(start_timeout=start_timeout)
        finally:
            self.core.set_state(previous if previous != "listening" else "idle")

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
                if core.config.barge_in and listener.has_wake_word:
                    # « Hey Jarvis » pendant qu'il parle ou réfléchit : il se tait et t'écoute.
                    # Seuil plus exigeant, pour ne pas se déclencher sur sa propre voix.
                    strict = min(0.95, listener.wake_threshold + 0.2)
                    if listener.wait_for_wake_word(timeout=0.3, threshold=strict, adapt=False):
                        core.stop_speaking()
                        core.bus.publish("barge_in")
                        self._push_to_talk.set()  # écoute dès que la réponse abandonnée est close
                else:
                    listener.drain()  # ne pas s'entendre soi-même
                continue

            if core.awaiting_follow_up:
                # Mode conversation : on peut enchaîner sans redire « Hey Jarvis ».
                core.awaiting_follow_up = False
                self._push_to_talk.clear()  # déjà à l'écoute : pas de deuxième écoute derrière
                listener.flush()
                text = self._listen(core.config.follow_up_seconds)
                if text:
                    core.submit(text, "voice")
                continue

            triggered = self._push_to_talk.is_set()
            if not triggered and listener.has_wake_word:
                triggered = listener.wait_for_wake_word(timeout=0.3)
            elif not triggered:
                listener.drain()
            if not triggered:
                continue
            self._push_to_talk.clear()
            core.bus.publish("wake")
            beep()
            listener.flush()
            text = self._listen(6.0)
            if text:
                core.submit(text, "voice")


def beep() -> None:
    try:
        import numpy as np
        import sounddevice as sd

        t = np.linspace(0, 0.12, int(24000 * 0.12), endpoint=False)
        tone = 0.2 * np.sin(2 * np.pi * 880 * t) * np.hanning(t.size)
        sd.play(tone.astype(np.float32), samplerate=24000)
        sd.wait()
    except Exception:
        pass
