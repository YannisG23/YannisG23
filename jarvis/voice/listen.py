"""Écoute : micro, mot d'activation « Hey Jarvis », détection de fin de phrase et transcription Whisper."""

from __future__ import annotations

import queue
import re
import time

import numpy as np

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, la taille attendue par openWakeWord

# Phrases que Whisper « entend » parfois dans le silence ou le bruit.
_HALLUCINATIONS = re.compile(
    r"sous-titr|amara\.org|merci d'avoir regardé|abonnez-vous|thanks for watching|^\W*$",
    re.IGNORECASE,
)


class Listener:
    def __init__(self, whisper_model: str, language: str, wake_threshold: float = 0.5,
                 use_wake_model: bool = True, name: str = "") -> None:
        import sounddevice as sd
        from faster_whisper import WhisperModel

        self.language = language
        self.wake_threshold = wake_threshold
        self.stt = WhisperModel(whisper_model, device="auto", compute_type="int8")
        self.name = name
        # En mode « nom », pas besoin du modèle « Hey Jarvis » : la transcription suffit.
        self.wake = self._load_wake_model() if use_wake_model else None
        self._frames: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            callback=lambda data, *_: self._frames.put(data[:, 0].copy()),
        )
        self.level = 0.0  # niveau sonore du micro, pour l'animation du centre de commande
        self._stream.start()
        self.noise_floor = self._calibrate()

    @staticmethod
    def _load_wake_model():
        try:
            import openwakeword
            from openwakeword.model import Model

            openwakeword.utils.download_models(["hey_jarvis"])
            return Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception as exc:  # pas bloquant : on passe en mode « appuie sur Entrée »
            print(f"[Jarvis] Mot d'activation indisponible ({exc}). Mode appui sur Entrée.")
            return None

    @property
    def has_wake_word(self) -> bool:
        return self.wake is not None

    def _next_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Vide l'audio accumulé (ex. la voix de Jarvis captée pendant qu'il parlait)."""
        while not self._frames.empty():
            self._frames.get_nowait()
        if self.wake is not None:
            self.wake.reset()

    def _calibrate(self, seconds: float = 1.0) -> float:
        levels = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            frame = self._next_frame()
            if frame is not None:
                levels.append(_rms(frame))
        return float(np.median(levels)) if levels else 200.0

    def wait_for_wake_word(self, timeout: float | None = None, threshold: float | None = None,
                           adapt: bool = True) -> bool:
        """Renvoie True si « Hey Jarvis » est entendu avant la fin du délai."""
        threshold = self.wake_threshold if threshold is None else threshold
        deadline = None if timeout is None else time.monotonic() + timeout
        while deadline is None or time.monotonic() < deadline:
            frame = self._next_frame(timeout=0.2)
            if frame is None:
                continue
            self._observe(frame, adapt=adapt)
            scores = self.wake.predict(frame)
            if max(scores.values(), default=0.0) >= threshold:
                self.flush()
                return True
        return False

    def drain(self) -> None:
        """Consomme l'audio en attente sans l'analyser (quand Jarvis parle ou réfléchit)."""
        while True:
            frame = self._next_frame(timeout=0.05)
            if frame is None:
                return
            self._observe(frame, adapt=False)  # pas d'adaptation : Jarvis parle peut-être

    def _observe(self, frame: np.ndarray, adapt: bool = True) -> None:
        """Met à jour le niveau affiché et, en silence, suit l'évolution du bruit de fond."""
        self.level = _rms(frame)
        if adapt and self.level < self.speech_threshold:
            self.noise_floor = 0.995 * self.noise_floor + 0.005 * self.level

    @property
    def speech_threshold(self) -> float:
        return max(self.noise_floor * 3.0, 250.0)

    def record_utterance(self, start_timeout: float = 6.0, max_seconds: float = 20.0,
                         end_silence: float = 0.9) -> np.ndarray | None:
        """Enregistre une phrase : attend qu'on parle, puis s'arrête après un silence."""
        threshold = self.speech_threshold
        frames: list[np.ndarray] = []
        started = False
        silent_for = 0.0
        waited = 0.0
        frame_seconds = FRAME / SAMPLE_RATE
        while True:
            frame = self._next_frame()
            if frame is None:
                continue
            # Avant qu'on parle, le silence sert aussi à suivre le bruit de fond.
            self._observe(frame, adapt=not started)
            loud = self.level > threshold
            if not started:
                waited += frame_seconds
                frames = (frames + [frame])[-4:]  # garde un peu d'audio avant le début
                if loud:
                    started = True
                elif waited >= start_timeout:
                    return None
                continue
            frames.append(frame)
            silent_for = 0.0 if loud else silent_for + frame_seconds
            if silent_for >= end_silence or len(frames) * frame_seconds >= max_seconds:
                return np.concatenate(frames)

    def transcribe(self, audio: np.ndarray, fast: bool = False) -> str:
        """fast=True : passe rapide, pour repérer le nom dans ce qui se dit autour du micro."""
        segments, _ = self.stt.transcribe(
            audio.astype(np.float32) / 32768.0,
            language=self.language,
            beam_size=1 if fast else 5,
            vad_filter=True,
            # Aide Whisper à bien écrire le nom de l'assistant.
            initial_prompt=f"{self.name}," if self.name else None,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return "" if _HALLUCINATIONS.search(text) else text

    def listen(self, start_timeout: float = 6.0) -> str:
        audio = self.record_utterance(start_timeout=start_timeout)
        return self.transcribe(audio) if audio is not None else ""

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
