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
    def __init__(self, whisper_model: str, language: str, wake_threshold: float = 0.5) -> None:
        import sounddevice as sd
        from faster_whisper import WhisperModel

        self.language = language
        self.wake_threshold = wake_threshold
        self.stt = WhisperModel(whisper_model, device="auto", compute_type="int8")
        self.wake = self._load_wake_model()
        self._frames: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            callback=lambda data, *_: self._frames.put(data[:, 0].copy()),
        )
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

    def wait_for_wake_word(self) -> None:
        self.flush()
        while True:
            frame = self._next_frame()
            if frame is None:
                continue
            scores = self.wake.predict(frame)
            if max(scores.values(), default=0.0) >= self.wake_threshold:
                self.flush()
                return

    def record_utterance(self, start_timeout: float = 6.0, max_seconds: float = 20.0,
                         end_silence: float = 0.9) -> np.ndarray | None:
        """Enregistre une phrase : attend qu'on parle, puis s'arrête après un silence."""
        threshold = max(self.noise_floor * 3.0, 400.0)
        frames: list[np.ndarray] = []
        started = False
        silent_for = 0.0
        waited = 0.0
        frame_seconds = FRAME / SAMPLE_RATE
        while True:
            frame = self._next_frame()
            if frame is None:
                continue
            loud = _rms(frame) > threshold
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

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.stt.transcribe(
            audio.astype(np.float32) / 32768.0,
            language=self.language,
            beam_size=5,
            vad_filter=True,
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
