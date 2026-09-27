"""Synthèse vocale : voix neuronale Microsoft Edge (gratuite, en ligne), repli hors ligne pyttsx3."""

from __future__ import annotations

import asyncio
import re
import threading


def clean_for_speech(text: str) -> str:
    """Retire ce qui se lit mal à voix haute : markdown, URL, émojis, références."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [texte](lien) -> texte
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, voice: str, rate: str = "+0%") -> None:
        self.voice = voice
        self.rate = rate
        self._lock = threading.Lock()
        self._offline = None

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        with self._lock:
            try:
                self._say_edge(text)
            except Exception:
                # Pas d'internet ou service indisponible : voix hors ligne.
                self._say_offline(text)

    def _say_edge(self, text: str) -> None:
        import edge_tts
        import miniaudio
        import numpy as np
        import sounddevice as sd

        async def synthesize() -> bytes:
            audio = bytearray()
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
            return bytes(audio)

        mp3 = asyncio.run(synthesize())
        decoded = miniaudio.decode(
            mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000
        )
        samples = np.array(decoded.samples, dtype=np.int16)
        sd.play(samples, samplerate=24000)
        sd.wait()

    def _say_offline(self, text: str) -> None:
        import pyttsx3

        if self._offline is None:
            self._offline = pyttsx3.init()
        self._offline.say(text)
        self._offline.runAndWait()


class SilentSpeaker:
    """Utilisé en mode texte : n'émet aucun son."""

    def say(self, text: str) -> None:  # noqa: D401
        pass
