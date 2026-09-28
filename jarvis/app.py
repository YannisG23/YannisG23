"""Lancement de Jarvis : noyau + centre de commande + voix + terminal."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from datetime import datetime

from rich.console import Console
from rich.markup import escape
from rich.text import Text
from rich.panel import Panel

from .config import Config
from .core import Core, is_yes

console = Console()
_EXIT = re.compile(r"^\W*(quit|exit|au revoir|bonne nuit|éteins-toi|arrête-toi)\W*(jarvis)?\W*$", re.I)


def _print_events(core: Core, pending: dict) -> None:
    """Affiche dans le terminal ce qui se passe (miroir du centre de commande)."""
    q = core.bus.subscribe()
    while True:
        ev = q.get()
        kind, d = ev["type"], ev["data"]
        if kind == "user_message":
            icon = "🎙" if d.get("source") == "voice" else "›"
            console.print(f"[bold cyan]Toi {icon}[/] {escape(d['text'])}")
        elif kind == "assistant_message" and d.get("text"):
            console.print(Panel(Text(d["text"]), title="[bold]J.A.R.V.I.S.[/]", border_style="bright_blue", expand=False))
        elif kind == "tool_start":
            args = escape(", ".join(f"{k}={str(v)[:50]}" for k, v in (d.get("args") or {}).items()))
            console.print(f"[dim]  ⚙ {d['name']}({args})[/]")
        elif kind == "notification":
            console.print(f"[bold yellow]🔔 {escape(d['text'])}[/]")
        elif kind == "error":
            console.print(f"[red]⚠ {escape(d['message'])}[/]")
        elif kind == "consolidated":
            console.print(f"[dim]🧠 Conversation mémorisée ({d['facts']} nouveaux faits) : {escape(d['summary'])}[/]")
        elif kind == "confirm_request":
            pending["id"] = d["id"]
            console.print(f"[bold yellow]⚠ Jarvis veut {escape(d['action'])}. Réponds o/n (ou à la voix, ou dans le centre de commande).[/]")
        elif kind == "confirm_resolved":
            if pending.get("id") == d["id"]:
                pending.clear()
            console.print(f"[dim]  {'✓ autorisé' if d.get('approved') else '✗ refusé'}[/]")
        elif kind == "state" and d["state"] == "listening":
            console.print("[green]  … j'écoute[/]")


def _greeting(config: Config, now: datetime) -> str:
    if now.hour < 5 or now.hour >= 22:
        return f"Encore debout, {config.user_name} ? Je suis là si tu as besoin."
    if now.hour < 12:
        return f"Bonjour {config.user_name}. Tous les systèmes sont opérationnels."
    if now.hour < 18:
        return f"Bon après-midi {config.user_name}. Je suis prêt."
    return f"Bonsoir {config.user_name}. Tous les systèmes sont opérationnels."


def _first_launch_today(config: Config, now: datetime) -> bool:
    """Vrai au premier lancement de la journée (retenu dans ~/.jarvis/state.json)."""
    path = config.home / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    today = now.date().isoformat()
    if state.get("last_start_day") == today:
        return False
    state["last_start_day"] = today
    path.write_text(json.dumps(state), encoding="utf-8")
    return True


def _start_voice(config: Config, core: Core):
    """Démarre l'écoute. En cas de souci (pas de micro, dépendance absente), renvoie None."""
    from .voice.listen import Listener
    from .voice.loop import VoiceLoop

    try:
        with console.status("[bright_blue]Chargement de la reconnaissance vocale…[/]"):
            listener = Listener(config.whisper_model, config.language, config.wake_threshold)
    except Exception as exc:
        console.print(f"[red]Micro ou reconnaissance vocale indisponible : {escape(str(exc))}[/]\n"
                      "[yellow]Je continue au clavier et dans le centre de commande. "
                      "Lance « python -m jarvis --doctor » pour diagnostiquer.[/]")
        return None
    core.voice = VoiceLoop(core, listener)
    return listener


def run(config: Config, voice: bool = True, dashboard: bool = True, open_browser: bool = True) -> None:
    speaker = None
    if voice:
        try:
            from .voice.speak import Speaker

            if config.tts_engine == "elevenlabs" and not config.elevenlabs_api_key:
                console.print("[yellow]JARVIS_TTS=elevenlabs mais ELEVENLABS_API_KEY est vide : voix gratuite.[/]")
            speaker = Speaker.from_config(config)
        except Exception as exc:
            console.print(f"[red]Synthèse vocale indisponible : {escape(str(exc))}[/]")
    try:
        core = Core(config, speaker=speaker)
    except ImportError:
        console.print("[red]Le mode abonnement nécessite claude-agent-sdk : relance install.bat "
                      "(ou pip install -e \".[all]\").[/]")
        return
    if speaker is not None:
        speaker.on_warning = lambda message: core.bus.publish("error", {"message": message})
    listener = _start_voice(config, core) if voice else None

    pending: dict = {}
    threading.Thread(target=_print_events, args=(core, pending), daemon=True).start()

    board = None
    if dashboard:
        from .dashboard.server import Dashboard

        try:
            board = Dashboard(core, config.dashboard_port)
            board.start()
        except OSError as exc:
            console.print(f"[red]Centre de commande indisponible (port {config.dashboard_port} occupé ?) : {escape(str(exc))}[/]")
            board = None

    lines = []
    if board:
        lines.append(f"Centre de commande : [link={board.url}]{board.url}[/link]")
    if listener:
        lines.append("Dis « Hey Jarvis » pour me parler." if listener.has_wake_word
                     else "Appuie sur Entrée (champ vide) pour me parler.")
    lines.append("Tu peux aussi écrire ici. « quit » pour quitter.")
    console.print(Panel("\n".join(lines), title="J.A.R.V.I.S. en ligne", border_style="bright_blue"))
    if board and open_browser:
        webbrowser.open(board.url)

    if core.voice:
        core.voice.start()
    now = datetime.now()
    if config.daily_briefing and 5 <= now.hour < 14 and _first_launch_today(config, now):
        core.briefing(source="system")  # premier lancement du matin : Jarvis fait le point
    else:
        greeting = _greeting(config, now)
        core.bus.publish("assistant_message", {"text": greeting})
        core.say(greeting)

    try:
        while True:
            text = console.input("").strip()
            if pending.get("id") and text:
                core.resolve_confirm(pending["id"], text.lower() in {"o", "y"} or is_yes(text))
                continue
            if not text:
                if core.voice and not listener.has_wake_word:
                    core.voice.push_to_talk()
                continue
            if _EXIT.match(text):
                break
            if text.lower() == "stop":
                core.stop_speaking()
                continue
            core.submit(text, "text")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        console.print("[dim]Je range la conversation dans ma mémoire… À bientôt.[/]")
        core.say("À bientôt.")
        core.speaker.wait(timeout=5)
        if core.voice:
            core.voice.stop()
        if listener:
            listener.close()
        if board:
            board.stop()
        core.shutdown()
