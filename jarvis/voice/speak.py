"""Synthèse vocale naturelle (voix neuronales Microsoft Edge), avec repli hors ligne.

Les phrases sont mises en file : pendant qu'une phrase est jouée, la suivante est déjà
synthétisée, ce qui donne une élocution fluide dès le premier mot de la réponse.
"""

from __future__ import annotations

import asyncio
import queue
import re
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 24000


def clean_for_speech(text: str) -> str:
    """Retire ce qui se lit mal à voix haute : markdown, URL, émojis, références."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [texte](lien) -> texte
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_mp3(mp3: bytes) -> np.ndarray:
    import miniaudio

    decoded = miniaudio.decode(
        mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=SAMPLE_RATE
    )
    return np.array(decoded.samples, dtype=np.int16)


def synth_edge(text: str, voice: str, rate: str = "+0%") -> np.ndarray:
    """Synthétise une phrase avec une voix neuronale Microsoft Edge (gratuit, nécessite internet)."""
    import edge_tts

    async def synthesize() -> bytes:
        audio = bytearray()
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    return _decode_mp3(asyncio.run(synthesize()))


def _envelope(audio: np.ndarray, step: int = int(SAMPLE_RATE * 0.04)) -> np.ndarray:
    frames = audio[: len(audio) // step * step].astype(np.float32).reshape(-1, step)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) if len(frames) else np.zeros(0)
    return np.clip(rms / 6000.0, 0.0, 1.0)


class ElevenLabsQuotaError(RuntimeError):
    """Clé refusée ou crédits épuisés : inutile de réessayer pendant la session."""


def synth_elevenlabs(text: str, api_key: str, voice_id: str, model: str) -> np.ndarray:
    """Synthétise une phrase avec ElevenLabs (voix premium, payante au-delà du quota gratuit)."""
    import requests

    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": api_key, "accept": "audio/mpeg"},
        json={"text": text, "model_id": model},
        timeout=20,
    )
    if response.status_code in (401, 402, 403, 429):
        try:
            detail = response.json().get("detail", {})
            message = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = response.text[:200]
        raise ElevenLabsQuotaError(message or f"erreur {response.status_code}")
    response.raise_for_status()
    return _decode_mp3(response.content)


class Speaker:
    def __init__(self, voice: str, rate: str = "+0%",
                 on_state: Callable[[bool], None] | None = None,
                 elevenlabs: dict | None = None,
                 on_warning: Callable[[str], None] | None = None) -> None:
        import sounddevice  # noqa: F401  (échoue tout de suite s'il n'y a pas d'audio)

        self.voice = voice
        self.rate = rate
        # {"api_key", "voice_id", "model"} pour utiliser ElevenLabs, sinon voix Edge gratuite.
        self.elevenlabs = elevenlabs if elevenlabs and elevenlabs.get("api_key") else None
        self.on_warning = on_warning or (lambda message: None)
        self.on_play: Callable[[str], None] = lambda text: None  # phrase qui commence à être dite
        self._envelope: np.ndarray | None = None  # volume de la phrase en cours, par tranches de 40 ms
        self._play_started = 0.0
        self.on_state = on_state or (lambda speaking: None)
        self._texts: queue.Queue[tuple[int, str]] = queue.Queue()
        self._audio: queue.Queue[tuple[int, np.ndarray, str]] = queue.Queue()
        self._generation = 0  # incrémenté par stop() pour jeter ce qui est en attente
        self._pending = 0
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._offline = None
        threading.Thread(target=self._synth_loop, daemon=True, name="tts-synth").start()
        threading.Thread(target=self._play_loop, daemon=True, name="tts-play").start()

    @classmethod
    def from_config(cls, config) -> "Speaker":
        premium = None
        if config.tts_engine == "elevenlabs" and config.elevenlabs_api_key:
            premium = {"api_key": config.elevenlabs_api_key, "voice_id": config.elevenlabs_voice_id,
                       "model": config.elevenlabs_model}
        return cls(config.tts_voice, config.tts_rate, elevenlabs=premium)

    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    @property
    def level(self) -> float:
        """Volume de la voix en ce moment, entre 0 et 1 (pour l'animation)."""
        envelope = self._envelope
        if envelope is None:
            return 0.0
        index = int((time.monotonic() - self._play_started) / 0.04)
        return float(envelope[index]) if 0 <= index < len(envelope) else 0.0

    def say(self, text: str) -> None:
        """Ajoute une phrase à dire (non bloquant)."""
        text = clean_for_speech(text)
        if not text:
            return
        with self._lock:
            self._pending += 1
            if self._idle.is_set():
                self._idle.clear()
                self.on_state(True)
            self._texts.put((self._generation, text))

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def stop(self) -> None:
        """Coupe la parole immédiatement."""
        import sounddevice as sd

        with self._lock:
            self._generation += 1
        sd.stop()

    def _done_one(self) -> None:
        with self._lock:
            self._pending -= 1
            if self._pending <= 0:
                self._pending = 0
                self._idle.set()
                self.on_state(False)

    def _synth_loop(self) -> None:
        while True:
            generation, text = self._texts.get()
            audio = None
            if generation == self._generation:
                try:
                    audio = self._synth_premium(text)
                except Exception:
                    audio = None
                try:
                    if audio is None:
                        audio = self._synth_edge(text)
                except Exception:
                    try:
                        audio = self._synth_offline(text)
                    except Exception:
                        audio = None
            if audio is None:
                self._done_one()
            else:
                self._audio.put((generation, audio, text))

    def _play_loop(self) -> None:
        import sounddevice as sd

        while True:
            generation, audio, text = self._audio.get()
            try:
                if generation == self._generation:
                    self._envelope = _envelope(audio)
                    self._play_started = time.monotonic()
                    self.on_play(text)
                    sd.play(audio, samplerate=SAMPLE_RATE)
                    sd.wait()
            except Exception:
                pass
            finally:
                self._envelope = None
                self._done_one()

    def _synth_premium(self, text: str) -> np.ndarray | None:
        if not self.elevenlabs:
            return None
        try:
            return synth_elevenlabs(text, self.elevenlabs["api_key"], self.elevenlabs["voice_id"],
                                    self.elevenlabs["model"])
        except ElevenLabsQuotaError as exc:
            self.elevenlabs = None  # plus de crédits : voix gratuite pour le reste de la session
            self.on_warning(f"ElevenLabs indisponible ({exc}) : je passe sur la voix gratuite.")
            return None

    def _synth_edge(self, text: str) -> np.ndarray:
        return synth_edge(text, self.voice, self.rate)

    def _synth_offline(self, text: str) -> np.ndarray:
        """Voix du système (hors ligne), rendue dans un fichier WAV puis rééchantillonnée."""
        import pyttsx3

        if self._offline is None:
            self._offline = pyttsx3.init()
        path = Path(tempfile.gettempdir()) / "jarvis_tts.wav"
        self._offline.save_to_file(text, str(path))
        self._offline.runAndWait()
        with wave.open(str(path)) as wav:
            rate, channels = wav.getframerate(), wav.getnchannels()
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if rate != SAMPLE_RATE and samples.size:
            positions = np.linspace(0, samples.size - 1, int(samples.size * SAMPLE_RATE / rate))
            samples = np.interp(positions, np.arange(samples.size), samples).astype(np.int16)
        return samples


class SilentSpeaker:
    """Utilisé sans audio (mode texte) : même interface, aucun son."""

    speaking = False
    level = 0.0

    def say(self, text: str) -> None:
        pass

    def wait(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None:
        pass
