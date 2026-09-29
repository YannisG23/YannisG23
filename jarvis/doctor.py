"""Diagnostic : python -m jarvis --doctor vérifie chaque brique et dit comment réparer."""

from __future__ import annotations

import importlib
import socket
import sys
import time
from typing import Callable

from rich.console import Console
from rich.markup import escape

from .config import Config

console = Console()


class Check:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def run(self, title: str, test: Callable[[], str], fix: str = "", optional: bool = False) -> bool:
        with console.status(f"[bright_blue]{title}…[/]"):
            try:
                detail = test()
                ok = True
            except Exception as exc:  # chaque vérification est indépendante
                detail = f"{type(exc).__name__}: {exc}"
                ok = False
        detail, fix = escape(detail), escape(fix)
        if ok:
            console.print(f"[green]✓[/] {title}" + (f" [dim]— {detail}[/]" if detail else ""))
            return True
        if optional:
            self.warnings += 1
            console.print(f"[yellow]•[/] {title} [dim]— {detail}[/]")
        else:
            self.failures += 1
            console.print(f"[red]✗[/] {title} [dim]— {detail}[/]")
        if fix:
            console.print(f"    [yellow]→ {fix}[/]")
        return False


def _python() -> str:
    if sys.version_info < (3, 10):
        raise RuntimeError(f"Python {sys.version.split()[0]} trop ancien")
    version = sys.version.split()[0]
    if sys.version_info >= (3, 13):
        return f"Python {version} (si une dépendance vocale refuse de s'installer, prends Python 3.11 ou 3.12)"
    return f"Python {version}"


def _imports(names: list[str]) -> Callable[[], str]:
    def test() -> str:
        missing = []
        for name in names:
            try:
                importlib.import_module(name)
            except Exception:
                missing.append(name)
        if missing:
            raise RuntimeError("manquant : " + ", ".join(missing))
        return ""
    return test


def _api(config: Config) -> Callable[[], str]:
    def test() -> str:
        import anthropic

        client = anthropic.Anthropic()
        model = client.models.retrieve(config.model)
        return f"clé valide, modèle {model.id} disponible"
    return test


def _subscription() -> str:
    from .brain_subscription import auth_status, find_claude_cli

    if not find_claude_cli():
        raise RuntimeError("Claude Code pour Windows (claude.exe) introuvable : double-clique sur connexion.bat")
    status = auth_status()
    if not status.get("loggedIn"):
        raise RuntimeError("compte Claude non connecté")
    method = status.get("authMethod", "")
    if "api" in str(method).lower():
        raise RuntimeError(f"connecté avec une clé API ({method}) et non avec ton abonnement")
    return "compte Claude connecté (abonnement)"


def _elevenlabs(config: Config) -> str:
    import requests

    if not config.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY est vide")
    response = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                            headers={"xi-api-key": config.elevenlabs_api_key}, timeout=15)
    if response.status_code == 401:
        raise RuntimeError("clé refusée")
    response.raise_for_status()
    data = response.json()
    used, limit = data.get("character_count", 0), data.get("character_limit", 0)
    return f"offre {data.get('tier', '?')}, {max(0, limit - used)} caractères restants ce mois-ci"


def _microphone() -> str:
    import numpy as np
    import sounddevice as sd

    device = sd.query_devices(kind="input")
    console.print("    [dim]Parle normalement pendant 3 secondes…[/]")
    audio = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    level = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    if level < 30:
        raise RuntimeError(f"« {device['name']} » ne capte presque rien (niveau {level:.0f})")
    return f"« {device['name']} », niveau {level:.0f}" + (" (un peu faible)" if level < 250 else "")


def _voice(config: Config) -> str:
    from .voice.speak import Speaker

    speaker = Speaker.from_config(config)
    speaker.say(f"Bonjour {config.user_name}. Ceci est un test de ma voix.")
    time.sleep(0.3)
    if not speaker.wait(timeout=30):
        raise RuntimeError("la lecture ne se termine pas")
    import sounddevice as sd

    voice = "ElevenLabs" if speaker.elevenlabs else config.tts_voice
    return f"voix {voice} sur « {sd.query_devices(kind='output')['name']} » (tu as dû l'entendre)"


def _edge_voice(config: Config) -> str:
    from .voice.speak import synth_edge

    if not len(synth_edge("Test.", config.tts_voice, config.tts_rate)):
        raise RuntimeError("audio vide")
    return "voix neuronale en ligne"


def _whisper(config: Config) -> str:
    from faster_whisper import WhisperModel

    start = time.monotonic()
    WhisperModel(config.whisper_model, device="auto", compute_type="int8")
    return f"modèle « {config.whisper_model} » chargé en {time.monotonic() - start:.0f} s"


def _wake_word() -> str:
    import openwakeword
    from openwakeword.model import Model

    openwakeword.utils.download_models(["hey_jarvis"])
    Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    return "« Hey Jarvis » prêt"


def _google(config: Config) -> str:
    from .tools.google_tools import authorize

    if not config.google_token.exists():
        where = "présent" if config.google_credentials.exists() else f"absent ({config.google_credentials})"
        raise RuntimeError(f"pas connecté, fichier OAuth {where}")
    authorize(config)
    return "Gmail et Agenda connectés"


def _port(config: Config) -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", config.dashboard_port))
    return f"port {config.dashboard_port} libre"


def _memory(config: Config) -> str:
    from .memory import Memory

    memory = Memory(config.memory_db)
    stats = memory.stats()
    memory.close()
    return f"{stats['facts']} faits, {stats['episodes']} conversations, {stats['open_tasks']} tâches ({config.memory_db})"


def run_doctor(config: Config) -> int:
    console.rule(f"[bold bright_blue]Diagnostic de {escape(config.assistant_name)}")
    check = Check()
    check.run("Python", _python, "Installe Python 3.11 ou 3.12 depuis python.org.")
    check.run("Bibliothèques de base", _imports(["anthropic", "claude_agent_sdk", "rich", "requests", "psutil", "numpy", "dotenv"]),
              'pip install -e ".[all]"')
    if config.uses_subscription:
        brain_ok = check.run("Cerveau : abonnement Claude", _subscription,
                             "Lance « python -m jarvis --login » et connecte-toi avec ton compte Claude (Pro ou Max). "
                             "Pour utiliser une clé API à la place : JARVIS_BRAIN=api dans .env.")
    else:
        brain_ok = check.run("Cerveau : clé API Anthropic", _api(config),
                             "Mets ANTHROPIC_API_KEY=sk-ant-... dans le fichier .env (console.anthropic.com → API keys). "
                             "Si la clé est bonne, vérifie JARVIS_MODEL et le crédit du compte.")
    check.run("Mémoire", lambda: _memory(config))
    check.run("Centre de commande", lambda: _port(config),
              "Un autre assistant tourne déjà ? Sinon change JARVIS_DASHBOARD_PORT.")
    check.run("Contrôle du PC", _imports(["pyautogui", "pyperclip", "PIL"]), 'pip install -e ".[all]"', optional=True)

    voice_libs = check.run("Bibliothèques vocales",
                           _imports(["sounddevice", "faster_whisper", "edge_tts", "miniaudio"]),
                           'pip install -e ".[voice]" (sous Linux : sudo apt install libportaudio2)')
    if voice_libs:
        from .voice.devices import apply as apply_devices

        for warning in apply_devices(config.mic_device, config.speaker_device):
            console.print(f"    [yellow]{escape(warning)}[/]")
        check.run("Voix neuronale (internet)", lambda: _edge_voice(config),
                  "Pas d'accès à la voix en ligne : il utilisera la voix du système (pyttsx3).",
                  optional=True)
        if config.tts_engine == "elevenlabs":
            check.run("Voix ElevenLabs", lambda: _elevenlabs(config),
                      "Vérifie ELEVENLABS_API_KEY (elevenlabs.io → Profile → API keys). "
                      "Sans elle, il utilise la voix gratuite.", optional=True)
        check.run("Haut-parleurs", lambda: _voice(config),
                  "Vérifie la sortie audio par défaut de ton système, ou choisis-en une : "
                  "python -m jarvis --micros puis JARVIS_SPEAKERS=<numéro> dans .env.")
        check.run("Micro", _microphone,
                  "Ce n'est peut-être pas le bon micro : python -m jarvis --micros pour voir la liste, "
                  "puis JARVIS_MIC=<numéro ou bout du nom> dans .env. Sur Mac, autorise le micro pour ton terminal.")
        check.run("Reconnaissance vocale (Whisper)", lambda: _whisper(config),
                  "Le premier chargement télécharge le modèle : il faut internet. "
                  "Essaie JARVIS_WHISPER_MODEL=base si ton PC est lent.")
        if config.wake_by_name:
            check.run("Activation", lambda: f"dis simplement « {config.assistant_name}, … » (mode nom)")
        else:
            check.run("Mot d'activation", _wake_word,
                      "Sans lui, clique sur le micro du centre de commande ou appuie sur Entrée. "
                      "Sous Linux, utilise Python 3.10 ou 3.11.", optional=True)
    check.run("Gmail et Agenda", lambda: _google(config),
              "Optionnel : suis la section « Connecter Gmail » du README puis python -m jarvis --setup-google.",
              optional=True)

    console.rule()
    if check.failures:
        console.print(f"[red]{check.failures} problème(s) à corriger[/] avant de lancer l'assistant.")
    elif check.warnings:
        console.print(f"[green]Prêt ![/] ({check.warnings} option(s) non disponible(s)). Lance : python -m jarvis")
    else:
        console.print("[green]Tout est prêt.[/] Lance : python -m jarvis")
    if not brain_ok:
        console.print("[dim]Sans cerveau connecté, il ne peut pas réfléchir.[/]")
    return 1 if check.failures else 0
