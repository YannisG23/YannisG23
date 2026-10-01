"""Configuration de Jarvis, lue depuis les variables d'environnement (ou un fichier .env)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    # override=True : le fichier .env l'emporte sur une variable Windows du même nom
    # (ex. une ancienne OPENAI_API_KEY enregistrée pour un autre outil).
    load_dotenv(override=True)
except ImportError:  # python-dotenv est optionnel
    pass


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass
class Config:
    # Cerveau : « abonnement » (ton compte Claude via Claude Code, prix fixe)
    # ou « api » (clé API Anthropic, facturée à l'usage).
    brain: str = field(default_factory=lambda: _env("JARVIS_BRAIN", "abonnement").lower())
    model: str = field(default_factory=lambda: _env("JARVIS_MODEL", "claude-opus-5"))
    # « low » : réponses rapides, idéal à la voix. « medium » ou « high » pour réfléchir plus longtemps.
    effort: str = field(default_factory=lambda: _env("JARVIS_EFFORT", "low"))
    max_tool_steps: int = field(default_factory=lambda: int(_env("JARVIS_MAX_TOOL_STEPS", "15")))
    # Au-delà, la conversation est résumée dans la mémoire puis repart à neuf.
    max_context_tokens: int = field(default_factory=lambda: int(_env("JARVIS_MAX_CONTEXT_TOKENS", "150000")))
    # Après ce délai sans échange, la conversation est consolidée dans la mémoire.
    idle_minutes: float = field(default_factory=lambda: float(_env("JARVIS_IDLE_MINUTES", "20")))

    # Identité
    # Le nom de l'assistant : c'est aussi le mot qui le réveille (« Jarvis, mets de la musique »).
    assistant_name: str = field(default_factory=lambda: _env("JARVIS_NAME", "Jarvis"))
    # Autres orthographes que la transcription pourrait produire, séparées par des virgules.
    name_aliases: list[str] = field(
        default_factory=lambda: [a.strip() for a in _env("JARVIS_NAME_ALIASES", "").split(",") if a.strip()]
    )
    user_name: str = field(default_factory=lambda: _env("JARVIS_USER_NAME", "Yannis"))
    language: str = field(default_factory=lambda: _env("JARVIS_LANGUAGE", "fr"))
    country: str = field(default_factory=lambda: _env("JARVIS_COUNTRY", ""))
    city: str = field(default_factory=lambda: _env("JARVIS_CITY", ""))

    # Voix
    tts_voice: str = field(default_factory=lambda: _env("JARVIS_VOICE", "fr-FR-RemyMultilingualNeural"))
    tts_rate: str = field(default_factory=lambda: _env("JARVIS_VOICE_RATE", "+5%"))
    # Moteur de voix : « edge » (gratuit), « elevenlabs » ou « openai » (voix premium, clé requise).
    # « auto » : ElevenLabs dès qu'une clé ELEVENLABS_API_KEY est présente, sinon la voix gratuite.
    tts_engine: str = field(default_factory=lambda: _env("JARVIS_TTS", "auto").lower())
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY", ""))
    # « Daniel » : voix masculine posée à l'accent britannique, multilingue.
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9"))
    elevenlabs_model: str = field(default_factory=lambda: _env("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    # Voix OpenAI : JARVIS_TTS=openai + une clé API (platform.openai.com, facturée à l'usage,
    # indépendante de l'abonnement ChatGPT Plus).
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    openai_voice: str = field(default_factory=lambda: _env("OPENAI_VOICE", "ash"))
    openai_tts_model: str = field(default_factory=lambda: _env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"))
    openai_voice_instructions: str = field(default_factory=lambda: _env(
        "OPENAI_VOICE_INSTRUCTIONS",
        "Parle en français naturel, sur un ton chaleureux et détendu, comme un ami attentionné."))
    # Couper la parole à l'assistant pendant qu'il parle : « voix » (il suffit de parler : idéal au casque),
    # « nom » (il faut l'appeler par son nom : à préférer avec des enceintes) ou « 0 » (jamais).
    barge_in: str = field(default_factory=lambda: _env("JARVIS_BARGE_IN", "voix").lower())
    # Activation : « nom » (dire son nom, naturellement) ou « hey » (« Hey Jarvis », plus léger pour le PC).
    wake_mode: str = field(default_factory=lambda: _env("JARVIS_WAKE_MODE", "nom").lower())
    whisper_model: str = field(default_factory=lambda: _env("JARVIS_WHISPER_MODEL", "small"))
    # Reconnaissance de la parole : « auto » = transcription en ligne d'OpenAI (bien plus fiable, ~0,003 $
    # la minute) dès qu'une clé OPENAI_API_KEY est présente, Whisper sur le PC en secours ; « local » pour
    # tout garder sur le PC. Le nom, lui, est toujours repéré sur le PC : rien ne part tant qu'on ne l'appelle pas.
    stt: str = field(default_factory=lambda: _env("JARVIS_STT", "auto").lower())
    stt_model: str = field(default_factory=lambda: _env("JARVIS_STT_MODEL", "gpt-4o-mini-transcribe"))
    wake_threshold: float = field(default_factory=lambda: float(_env("JARVIS_WAKE_THRESHOLD", "0.5")))
    follow_up_seconds: float = field(default_factory=lambda: float(_env("JARVIS_FOLLOW_UP_SECONDS", "6")))
    # Micro et sortie audio : vide = ceux par défaut de Windows ; sinon un numéro ou un bout du nom.
    mic_device: str = field(default_factory=lambda: _env("JARVIS_MIC", ""))
    speaker_device: str = field(default_factory=lambda: _env("JARVIS_SPEAKERS", ""))

    # Conversation : « auto » = ChatGPT parle et Claude agit dès qu'une clé OPENAI_API_KEY est présente,
    # « gpt » pour l'imposer, « claude » pour que Claude fasse tout.
    conversation: str = field(default_factory=lambda: _env("JARVIS_CONVERSATION", "auto").lower())
    # Modèle ChatGPT de conversation : petit et rapide (facturé à l'usage sur la clé API).
    gpt_model: str = field(default_factory=lambda: _env("JARVIS_GPT_MODEL", "gpt-4.1-mini"))

    # Budget mensuel OpenAI en dollars (au-delà, Claude reprend la conversation) et prix du modèle
    # en dollars par million de jetons (entrée / sortie), pour estimer la dépense.
    openai_budget: float = field(default_factory=lambda: float(_env("JARVIS_OPENAI_BUDGET", "10")))
    gpt_price_in: float = field(default_factory=lambda: float(_env("JARVIS_GPT_PRICE_IN", "0.40")))
    gpt_price_out: float = field(default_factory=lambda: float(_env("JARVIS_GPT_PRICE_OUT", "1.60")))

    # Délégation à Codex (abonnement ChatGPT) pour économiser le quota Claude :
    # « off » (seulement sur demande), « auto » (grosses tâches de code), « max » (dès que c'est possible).
    codex_delegation: str = field(default_factory=lambda: _env("JARVIS_CODEX_DELEGATION", "auto").lower())

    # Centre de commande
    dashboard_port: int = field(default_factory=lambda: int(_env("JARVIS_DASHBOARD_PORT", "8765")))
    # Briefing automatique au premier lancement de la matinée.
    daily_briefing: bool = field(
        default_factory=lambda: _env("JARVIS_DAILY_BRIEFING", "1").lower() not in {"0", "false", "non", "no"}
    )
    # Minutes d'avance pour prévenir d'un rendez-vous (0 = désactivé).
    event_reminder_minutes: int = field(default_factory=lambda: int(_env("JARVIS_EVENT_REMINDER_MINUTES", "10")))

    # Données
    home: Path = field(
        default_factory=lambda: Path(_env("JARVIS_HOME", str(Path.home() / ".jarvis"))).expanduser()
    )

    def __post_init__(self) -> None:
        if self.tts_engine in {"auto", ""}:
            self.tts_engine = "elevenlabs" if self.elevenlabs_api_key else "edge"
        if self.barge_in in {"1", "true", "oui", "yes", ""}:
            self.barge_in = "voix"
        elif self.barge_in in {"0", "false", "non", "no", "off"}:
            self.barge_in = "off"
        elif self.barge_in not in {"voix", "nom"}:
            self.barge_in = "voix"
        if self.stt in {"auto", ""}:
            self.stt = "openai" if self.openai_api_key else "local"
        elif self.stt == "openai" and not self.openai_api_key:
            self.stt = "local"
        if self.codex_delegation not in {"off", "auto", "max"}:
            self.codex_delegation = "auto"
        self.home.mkdir(parents=True, exist_ok=True)
        # Un nom donné à la voix (« tu t'appelles Kali ») l'emporte sur le fichier .env.
        state = self.load_state()
        if state.get("assistant_name"):
            self.assistant_name = state["assistant_name"]
            self.name_aliases = list(state.get("name_aliases", []))

    # État mémorisé entre deux lancements (~/.jarvis/state.json).

    @property
    def state_file(self) -> Path:
        return self.home / "state.json"

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_state(self, **updates) -> None:
        state = self.load_state()
        state.update(updates)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    @property
    def model_is_explicit(self) -> bool:
        """Vrai si le modèle a été choisi dans .env (sinon l'abonnement choisit le sien)."""
        return bool(os.environ.get("JARVIS_MODEL", "").strip())

    @property
    def wake_by_name(self) -> bool:
        return self.wake_mode not in {"hey", "hey_jarvis", "openwakeword"}

    @property
    def gpt_conversation(self) -> bool:
        if self.conversation == "claude":
            return False
        return bool(self.openai_api_key)

    @property
    def uses_subscription(self) -> bool:
        return self.brain in {"abonnement", "subscription", "claude-code"}

    @property
    def memory_db(self) -> Path:
        return self.home / "memory.sqlite3"

    @property
    def google_credentials(self) -> Path:
        return self.home / "google_credentials.json"

    @property
    def google_token(self) -> Path:
        return self.home / "google_token.json"
