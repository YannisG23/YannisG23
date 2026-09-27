"""Configuration de Jarvis, lue depuis les variables d'environnement (ou un fichier .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv est optionnel
    pass


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass
class Config:
    # Cerveau
    model: str = field(default_factory=lambda: _env("JARVIS_MODEL", "claude-opus-5"))
    effort: str = field(default_factory=lambda: _env("JARVIS_EFFORT", "medium"))
    max_tool_steps: int = field(default_factory=lambda: int(_env("JARVIS_MAX_TOOL_STEPS", "15")))
    # Au-delà, la conversation est résumée dans la mémoire puis repart à neuf.
    max_context_tokens: int = field(default_factory=lambda: int(_env("JARVIS_MAX_CONTEXT_TOKENS", "150000")))
    # Après ce délai sans échange, la conversation est consolidée dans la mémoire.
    idle_minutes: float = field(default_factory=lambda: float(_env("JARVIS_IDLE_MINUTES", "20")))

    # Identité
    user_name: str = field(default_factory=lambda: _env("JARVIS_USER_NAME", "Yannis"))
    language: str = field(default_factory=lambda: _env("JARVIS_LANGUAGE", "fr"))
    country: str = field(default_factory=lambda: _env("JARVIS_COUNTRY", ""))
    city: str = field(default_factory=lambda: _env("JARVIS_CITY", ""))

    # Voix
    tts_voice: str = field(default_factory=lambda: _env("JARVIS_VOICE", "fr-FR-RemyMultilingualNeural"))
    tts_rate: str = field(default_factory=lambda: _env("JARVIS_VOICE_RATE", "+5%"))
    whisper_model: str = field(default_factory=lambda: _env("JARVIS_WHISPER_MODEL", "small"))
    wake_threshold: float = field(default_factory=lambda: float(_env("JARVIS_WAKE_THRESHOLD", "0.5")))
    follow_up_seconds: float = field(default_factory=lambda: float(_env("JARVIS_FOLLOW_UP_SECONDS", "6")))

    # Centre de commande
    dashboard_port: int = field(default_factory=lambda: int(_env("JARVIS_DASHBOARD_PORT", "8765")))
    # Minutes d'avance pour prévenir d'un rendez-vous (0 = désactivé).
    event_reminder_minutes: int = field(default_factory=lambda: int(_env("JARVIS_EVENT_REMINDER_MINUTES", "10")))

    # Données
    home: Path = field(
        default_factory=lambda: Path(_env("JARVIS_HOME", str(Path.home() / ".jarvis"))).expanduser()
    )

    def __post_init__(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)

    @property
    def memory_db(self) -> Path:
        return self.home / "memory.sqlite3"

    @property
    def google_credentials(self) -> Path:
        return self.home / "google_credentials.json"

    @property
    def google_token(self) -> Path:
        return self.home / "google_token.json"
