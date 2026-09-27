"""Lancement de Jarvis : noyau + centre de commande + voix + terminal."""

from __future__ import annotations

import re
import threading
import webbrowser

from rich.console import Console
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
            console.print(f"[bold cyan]Toi {icon}[/] {d['text']}")
        elif kind == "assistant_message" and d.get("text"):
            console.print(Panel(d["text"], title="[bold]J.A.R.V.I.S.[/]", border_style="bright_blue", expand=False))
        elif kind == "tool_start":
            args = ", ".join(f"{k}={str(v)[:50]}" for k, v in (d.get("args") or {}).items())
            console.print(f"[dim]  ⚙ {d['name']}({args})[/]")
        elif kind == "notification":
            console.print(f"[bold yellow]🔔 {d['text']}[/]")
        elif kind == "error":
            console.print(f"[red]⚠ {d['message']}[/]")
        elif kind == "consolidated":
            console.print(f"[dim]🧠 Conversation mémorisée ({d['facts']} nouveaux faits) : {d['summary']}[/]")
        elif kind == "confirm_request":
            pending["id"] = d["id"]
            console.print(f"[bold yellow]⚠ Jarvis veut {d['action']}. Réponds o/n (ou à la voix, ou dans le centre de commande).[/]")
        elif kind == "confirm_resolved":
            if pending.get("id") == d["id"]:
                pending.clear()
            console.print(f"[dim]  {'✓ autorisé' if d.get('approved') else '✗ refusé'}[/]")
        elif kind == "state" and d["state"] == "listening":
            console.print("[green]  … j'écoute[/]")


def run(config: Config, voice: bool = True, dashboard: bool = True, open_browser: bool = True) -> None:
    speaker = None
    listener = None
    if voice:
        from .voice.speak import Speaker

        speaker = Speaker(config.tts_voice, config.tts_rate)
    core = Core(config, speaker=speaker)

    if voice:
        from .voice.listen import Listener
        from .voice.loop import VoiceLoop

        with console.status("[bright_blue]Chargement de la reconnaissance vocale…[/]"):
            listener = Listener(config.whisper_model, config.language, config.wake_threshold)
        core.voice = VoiceLoop(core, listener)

    pending: dict = {}
    threading.Thread(target=_print_events, args=(core, pending), daemon=True).start()

    board = None
    if dashboard:
        from .dashboard.server import Dashboard

        try:
            board = Dashboard(core, config.dashboard_port)
            board.start()
        except OSError as exc:
            console.print(f"[red]Centre de commande indisponible (port {config.dashboard_port} occupé ?) : {exc}[/]")
            board = None

    lines = []
    if board:
        lines.append(f"Centre de commande : [link={board.url}]{board.url}[/link]")
    if voice:
        lines.append("Dis « Hey Jarvis » pour me parler." if listener.has_wake_word
                     else "Appuie sur Entrée (champ vide) pour me parler.")
    lines.append("Tu peux aussi écrire ici. « quit » pour quitter.")
    console.print(Panel("\n".join(lines), title="J.A.R.V.I.S. en ligne", border_style="bright_blue"))
    if board and open_browser:
        webbrowser.open(board.url)

    if core.voice:
        core.voice.start()
    greeting = f"Bonjour {config.user_name}. Tous les systèmes sont opérationnels."
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
