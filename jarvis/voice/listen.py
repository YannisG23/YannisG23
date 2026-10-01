"""Écoute : micro, mot d'activation « Hey Jarvis », détection de fin de phrase et transcription Whisper."""

from __future__ import annotations

import io
import os
import queue
import re
import sys
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, la taille attendue par openWakeWord
# Prix de la transcription en ligne, en dollars par minute d'audio.
STT_PRICES = {"gpt-4o-mini-transcribe": 0.003, "gpt-4o-transcribe": 0.006, "whisper-1": 0.006}

# Phrases que Whisper « entend » parfois dans le silence ou le bruit.
_HALLUCINATIONS = re.compile(
    r"sous-titr|amara\.org|merci d'avoir regardé|abonnez-vous|thanks for watching|^\W*$",
    re.IGNORECASE,
)


def _add_nvidia_dlls() -> None:
    """Windows : rend visibles les DLL CUDA installées par pip (paquets nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    if sys.platform != "win32":
        return
    import site

    bases = [*site.getsitepackages(), site.getusersitepackages()]
    for base in bases:
        for folder in Path(base).glob("nvidia/*/bin"):
            try:
                os.add_dll_directory(str(folder))
            except OSError:
                continue
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


def load_whisper(name: str) -> tuple[object, str]:
    """Charge Whisper sur la carte graphique NVIDIA si elle est utilisable, sinon sur le processeur.

    Un essai de transcription vérifie tout de suite que CUDA marche vraiment (les DLL manquantes
    ne se voient qu'à ce moment-là) et « chauffe » le modèle pour que la première phrase soit rapide.
    """
    from faster_whisper import WhisperModel

    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    _add_nvidia_dlls()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            model = WhisperModel(name, device="cuda", compute_type="float16")
            list(model.transcribe(silence, language="fr", beam_size=1)[0])
            return model, "cuda"
    except Exception:
        pass
    # Sur le processeur : un fil par cœur physique environ (par défaut, CTranslate2 n'en prend que 4).
    threads = max(4, (os.cpu_count() or 8) // 2)
    model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
    list(model.transcribe(silence, language="fr", beam_size=1)[0])
    return model, "cpu"


def to_wav(audio: np.ndarray) -> bytes:
    """Audio 16 kHz mono int16 → fichier WAV en mémoire."""
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(audio.astype(np.int16).tobytes())
    return buffer.getvalue()


def transcribe_openai(audio: np.ndarray, api_key: str, model: str, language: str, prompt: str = "") -> str:
    """Transcription en ligne d'OpenAI (clé API, facturée à la minute d'audio)."""
    import requests

    data = {"model": model, "language": language}
    if prompt:
        data["prompt"] = prompt
    response = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("phrase.wav", to_wav(audio), "audio/wav")},
        data=data,
        timeout=15,
    )
    response.raise_for_status()
    return str(response.json().get("text", "")).strip()


class Listener:
    def __init__(self, whisper_model: str, language: str, wake_threshold: float = 0.5,
                 use_wake_model: bool = True, name: str = "", openai_key: str = "",
                 stt_model: str = "gpt-4o-mini-transcribe", on_warning=None) -> None:
        import sounddevice as sd

        # Transcription en ligne (plus fiable) si une clé est fournie ; Whisper local en secours.
        self.openai_key = openai_key
        self.stt_model = stt_model
        self.on_warning = on_warning or (lambda message: None)
        self._online_failures = 0
        self.language = language
        self.wake_threshold = wake_threshold
        self.stt, self.device = load_whisper(whisper_model)
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
            print(f"[Assistant] Mot d'activation indisponible ({exc}). Mode appui sur Entrée.")
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

    def speech_onset(self, timeout: float = 0.3, min_speech: float = 0.3) -> np.ndarray | None:
        """Pendant qu'il parle : détecte qu'on lui parle (au moins min_speech secondes de voix).

        Renvoie le début de la phrase (pour ne pas le perdre), ou None. Au casque, sa propre voix
        n'arrive pas dans le micro : toute voix entendue est celle de l'utilisateur.
        """
        threshold = self.speech_threshold
        frame_seconds = FRAME / SAMPLE_RATE
        frames: list[np.ndarray] = []
        loud_for, gap = 0.0, 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline or frames:
            frame = self._next_frame(timeout=0.2)
            if frame is None:
                return None
            self._observe(frame, adapt=False)
            if self.level > threshold:
                frames.append(frame)
                loud_for += frame_seconds
                gap = 0
                if loud_for >= min_speech:
                    return np.concatenate(frames)
            elif frames:
                gap += 1
                frames.append(frame)
                if gap > 2:  # plus de 160 ms de silence : un bruit bref, pas une phrase
                    frames, loud_for, gap = [], 0.0, 0
        return None

    def record_utterance(self, start_timeout: float = 6.0, max_seconds: float = 20.0,
                         end_silence: float = 0.9, prefix: np.ndarray | None = None) -> np.ndarray | None:
        """Enregistre une phrase : attend qu'on parle, puis s'arrête après un silence.

        prefix : début de phrase déjà capté (on enchaîne directement sur la suite).
        """
        threshold = self.speech_threshold
        frames: list[np.ndarray] = [prefix] if prefix is not None else []
        started = prefix is not None
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
            if silent_for >= end_silence or sum(len(f) for f in frames) / SAMPLE_RATE >= max_seconds:
                return np.concatenate(frames)

    @property
    def online(self) -> bool:
        """Transcription en ligne utilisable (clé présente, budget OpenAI pas dépassé)."""
        if not self.openai_key or self._online_failures >= 3:
            return False
        from .. import usage

        return usage.current is None or not usage.current.openai_over_budget

    @property
    def precise_is_cheap(self) -> bool:
        """Une transcription soignée ne coûte qu'une fraction de seconde (en ligne ou carte graphique)."""
        return self.online or self.device == "cuda"

    def _transcribe_online(self, audio: np.ndarray) -> str | None:
        try:
            text = transcribe_openai(audio, self.openai_key, self.stt_model, self.language,
                                     prompt=f"{self.name}," if self.name else "")
        except Exception as exc:
            self._online_failures += 1
            if self._online_failures == 3:
                self.on_warning(f"Transcription en ligne indisponible ({exc}) : je repasse sur Whisper local.")
            return None
        self._online_failures = 0
        from .. import usage

        if usage.current is not None:
            minutes = len(audio) / SAMPLE_RATE / 60
            usage.current.record("stt", cost=minutes * STT_PRICES.get(self.stt_model, 0.006))
        return "" if _HALLUCINATIONS.search(text) else text

    def transcribe(self, audio: np.ndarray, fast: bool = False) -> str:
        """fast=True : passe rapide sur le PC, pour repérer le nom dans ce qui se dit autour du micro.

        Sinon (une vraie demande) : transcription en ligne si possible, Whisper local en secours.
        """
        if not fast and self.online:
            text = self._transcribe_online(audio)
            if text is not None:
                return text
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
