"""Lancement de Jarvis : noyau + centre de commande + voix + terminal."""

from __future__ import annotations

import os
import re
import threading
import time
import webbrowser
from datetime import datetime

from rich.console import Console
from rich.markup import escape
from rich.text import Text
from rich.panel import Panel

from .config import Config
from .core import Core, is_yes

console = Console()
# « quitter », « au revoir Nova »… : le nom éventuel après la formule est ignoré, quel qu'il soit.
_EXIT = re.compile(
    r"^\W*(quit|quitter|exit|sortir|au revoir|bonne nuit|[ée]teins[- ]toi|arr[êe]te[- ]toi|ferme[- ]toi)"
    r"\W*(\w+)?\W*$",
    re.I,
)


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
            console.print(Panel(Text(d["text"]), title=f"[bold]{escape(core.config.assistant_name)}[/]", border_style="bright_blue", expand=False))
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
            console.print(f"[bold yellow]⚠ {escape(core.config.assistant_name)} veut {escape(d['action'])}. Réponds o/n (ou à la voix, ou dans le centre de commande).[/]")
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
        return f"Bonjour {config.user_name}. Je suis prêt, dis-moi."
    if now.hour < 18:
        return f"Re-bonjour {config.user_name}. Qu'est-ce qu'on fait ?"
    return f"Bonsoir {config.user_name}. Je suis là."


NAMING_PROMPT = (
    "C'est notre toute première rencontre, {user} vient d'installer ton programme. Présente-toi en une ou "
    "deux phrases. Ensuite, {user} te laisse choisir toi-même ton nom : prends celui qui te plaît vraiment, "
    "et qui marche comme mot pour te réveiller (facile à prononcer, deux syllabes idéalement, pas un mot "
    "courant en français ni un prénom très répandu). Dis en une phrase pourquoi tu l'as choisi, puis "
    "utilise rename_assistant : {user} confirmera."
)


def _wait_forever() -> None:
    while True:
        time.sleep(3600)


def _needs_name(config: Config) -> bool:
    """Premier lancement sans nom choisi : l'assistant choisit le sien."""
    state = config.load_state()
    return not (state.get("assistant_name") or state.get("naming_done") or os.environ.get("JARVIS_NAME", "").strip())


def _first_launch_today(config: Config, now: datetime) -> bool:
    """Vrai au premier lancement de la journée (retenu dans ~/.jarvis/state.json)."""
    today = now.date().isoformat()
    if config.load_state().get("last_start_day") == today:
        return False
    config.save_state(last_start_day=today)
    return True


def _start_voice(config: Config, core: Core):
    """Démarre l'écoute. En cas de souci (pas de micro, dépendance absente), renvoie None."""
    from .voice.listen import Listener
    from .voice.loop import VoiceLoop

    try:
        with console.status("[bright_blue]Chargement de la reconnaissance vocale…[/]"):
            listener = Listener(config.whisper_model, config.language, config.wake_threshold,
                                use_wake_model=not config.wake_by_name, name=config.assistant_name)
    except Exception as exc:
        console.print(f"[red]Micro ou reconnaissance vocale indisponible : {escape(str(exc))}[/]\n"
                      "[yellow]Je continue au clavier et dans le centre de commande. "
                      "Lance « python -m jarvis --doctor » pour diagnostiquer.[/]")
        return None
    core.voice = VoiceLoop(core, listener)
    return listener


def _ensure_claude_login(config: Config) -> None:
    """Mode abonnement : si le compte Claude n'est pas connecté, on le connecte avant de démarrer."""
    if not config.uses_subscription:
        return
    try:
        from .brain_subscription import auth_status, login

        if auth_status().get("loggedIn"):
            return
    except Exception:
        return  # on ne bloque pas le démarrage : l'erreur précise s'affichera à la première question
    console.print(Panel("Ton compte Claude n'est pas encore connecté.\n"
                        "Une page va s'ouvrir dans ton navigateur : connecte-toi avec ton compte Claude "
                        "(Pro ou Max), puis reviens ici.", title="Connexion à Claude", border_style="yellow"))
    login()


def run(config: Config, voice: bool = True, dashboard: bool = True, open_browser: bool = True,
        window: bool = False) -> None:
    _ensure_claude_login(config)
    speaker = None
    if voice:
        try:
            from .voice.devices import apply as apply_devices

            for warning in apply_devices(config.mic_device, config.speaker_device):
                console.print(f"[yellow]{escape(warning)}[/]")
        except Exception:
            pass  # pas de sounddevice : la voix sera signalée indisponible juste après
        try:
            from .voice.speak import Speaker

            if config.tts_engine == "elevenlabs" and not config.elevenlabs_api_key:
                console.print("[yellow]JARVIS_TTS=elevenlabs mais ELEVENLABS_API_KEY est vide : voix gratuite.[/]")
            if config.tts_engine == "openai" and not config.openai_api_key:
                console.print("[yellow]JARVIS_TTS=openai mais OPENAI_API_KEY est vide : voix gratuite.[/]")
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
    if core.voice:
        lines.append(core.voice.activation_hint + ". Entrée (champ vide) marche aussi.")
    lines.append("Tu peux aussi écrire ici. « quit » pour quitter.")
    console.print(Panel("\n".join(lines), title=f"{escape(config.assistant_name)} est en ligne", border_style="bright_blue"))
    if window and not board:
        console.print("[red]Mode appli impossible sans centre de commande : j'ouvre le terminal seul.[/]")
        window = False
    if board and open_browser and not window:
        webbrowser.open(board.url)

    if core.voice:
        core.voice.start()
    now = datetime.now()
    if _needs_name(config):
        naming = core.submit(NAMING_PROMPT.format(user=config.user_name), "system")

        def remember_naming() -> None:
            # Une seule fois, mais seulement si la présentation a vraiment eu lieu
            # (sinon on la retente au prochain lancement).
            naming.done.wait()
            if naming.ok:
                config.save_state(naming_done=True)

        threading.Thread(target=remember_naming, daemon=True).start()
    elif config.daily_briefing and 5 <= now.hour < 14 and _first_launch_today(config, now):
        core.briefing(source="system")  # premier lancement du matin : Jarvis fait le point
    else:
        greeting = _greeting(config, now)
        core.bus.publish("assistant_message", {"text": greeting})
        core.say(greeting)

    def console_loop() -> None:
        while True:
            try:
                text = console.input("").strip()
            except EOFError:
                # Pas de clavier (lancé en arrière-plan ou par l'appli de bureau) : on continue
                # à la voix et dans le centre de commande, jusqu'à Ctrl+C ou la fermeture.
                if not (listener or board):
                    raise
                _wait_forever()
                break
            if pending.get("id") and text:
                core.resolve_confirm(pending["id"], text.lower() in {"o", "y"} or is_yes(text))
                continue
            if not text:
                if core.voice:
                    core.voice.push_to_talk()
                continue
            if _EXIT.match(text):
                return
            if text.lower() == "stop":
                core.stop_speaking()
                continue
            core.submit(text, "text")

    try:
        if window:
            # La fenêtre tourne dans son propre processus : si elle plante (WebView2, pilote graphique…),
            # Jarvis continue et bascule sur le navigateur ; ses erreurs vont dans fenetre.log.
            import subprocess
            import sys

            log_path = config.home / "fenetre.log"
            icon = config.home / "icon.ico"
            with open(log_path, "w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "jarvis.appwindow", board.url, config.assistant_name, str(icon)],
                    stdout=log, stderr=subprocess.STDOUT,
                )

                def terminal() -> None:
                    try:
                        console_loop()
                    except (KeyboardInterrupt, EOFError):
                        return
                    proc.terminate()  # « quitter » au clavier ferme aussi la fenêtre

                threading.Thread(target=terminal, daemon=True, name="jarvis-terminal").start()
                code = proc.wait()
            if code != 0:
                console.print(f"[red]La fenêtre de bureau s'est fermée sur une erreur (détail : {escape(str(log_path))}). "
                              "J'ouvre le centre de commande dans le navigateur ; Jarvis continue.[/]")
                core.bus.publish("error", {"message": f"Fenêtre de bureau : erreur, voir {log_path}"})
                webbrowser.open(board.url)
                console_loop()
        else:
            console_loop()
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
