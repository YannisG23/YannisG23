"""Boucles principales : mode vocal et mode texte."""

from __future__ import annotations

import re
import sys

import anthropic
from rich.console import Console
from rich.panel import Panel

from .brain import Brain, RefusalError
from .config import Config
from .memory import Memory
from .tools import registry

console = Console()

_YES = re.compile(r"\b(oui|ouais|yes|ok|okay|vas-y|go|confirme|d'accord|carrément|bien sûr|fais-le)\b", re.I)
_NO = re.compile(r"\b(non|no|annule|stop|surtout pas|pas du tout)\b", re.I)
_EXIT = re.compile(r"^\W*(au revoir|bonne nuit|à plus|éteins-toi|arrête-toi|quit|exit)\W*(jarvis)?\W*$", re.I)
_RESET = re.compile(r"nouvelle conversation|on repart de zéro|oublie cette conversation", re.I)


def is_yes(answer: str) -> bool:
    return bool(_YES.search(answer)) and not _NO.search(answer)


def show_user(text: str) -> None:
    console.print(f"[bold cyan]Toi[/] › {text}")


def show_jarvis(text: str) -> None:
    console.print(Panel(text, title="[bold]J.A.R.V.I.S.[/]", border_style="bright_blue", expand=False))


def show_tool(name: str, args: dict) -> None:
    summary = ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items())
    console.print(f"[dim]  ⚙ {name}({summary})[/]")


def _handle(brain: Brain, text: str, say) -> str:
    try:
        return brain.ask(text, on_text=say)
    except RefusalError as exc:
        return str(exc)
    except anthropic.AuthenticationError:
        return "Ma clé d'API Anthropic est invalide. Vérifie ANTHROPIC_API_KEY dans le fichier point env."
    except anthropic.RateLimitError:
        return "Je suis limité en nombre de requêtes pour le moment. Réessaie dans une minute."
    except anthropic.APIConnectionError:
        return "Je n'arrive pas à joindre mes serveurs. Vérifie la connexion internet."
    except anthropic.APIStatusError as exc:
        return f"Petit souci côté serveur, erreur {exc.status_code}."


def run_text(config: Config) -> None:
    memory = Memory(config.memory_db)

    def confirm(action: str) -> bool:
        answer = console.input(f"[yellow]Jarvis veut {action}. Tu confirmes ? (o/n)[/] ")
        return answer.strip().lower() in {"o", "oui", "y", "yes"} or is_yes(answer)

    brain = Brain(config, memory, registry, confirm=confirm, notify=show_jarvis, on_tool=show_tool)
    console.print(Panel(f"Mode texte. Tape « quit » pour sortir, « reset » pour une nouvelle conversation.",
                        title="J.A.R.V.I.S.", border_style="bright_blue"))
    while True:
        try:
            text = console.input("[bold cyan]Toi[/] › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"quit", "exit"} or _EXIT.match(text):
            show_jarvis("À bientôt.")
            break
        if text.lower() == "reset" or _RESET.search(text):
            brain.reset()
            show_jarvis("Nouvelle conversation. Je garde bien sûr ta mémoire long terme.")
            continue
        with console.status("[bright_blue]Jarvis réfléchit...[/]"):
            reply = _handle(brain, text, show_jarvis)
        show_jarvis(reply)


def run_voice(config: Config) -> None:
    from .voice.listen import Listener
    from .voice.speak import Speaker

    memory = Memory(config.memory_db)
    speaker = Speaker(config.tts_voice, config.tts_rate)
    with console.status("[bright_blue]Chargement de la reconnaissance vocale...[/]"):
        listener = Listener(config.whisper_model, config.language, config.wake_threshold)

    def say(text: str) -> None:
        show_jarvis(text)
        speaker.say(text)
        listener.flush()  # ne pas s'entendre soi-même

    def confirm(action: str) -> bool:
        say(f"Je dois {action}. Tu confirmes ?")
        answer = listener.listen(start_timeout=7)
        show_user(answer or "(pas de réponse)")
        return is_yes(answer)

    brain = Brain(config, memory, registry, confirm=confirm, notify=say, on_tool=show_tool)
    trigger = "Dis « Hey Jarvis »" if listener.has_wake_word else "Appuie sur Entrée"
    console.print(Panel(f"{trigger} pour me parler. Ctrl+C pour quitter.",
                        title="J.A.R.V.I.S. en ligne", border_style="bright_blue"))
    say(f"Bonjour {config.user_name}. Tous les systèmes sont opérationnels.")

    try:
        while True:
            if listener.has_wake_word:
                listener.wait_for_wake_word()
            else:
                console.input("[dim]Entrée pour parler...[/]")
                listener.flush()
            _beep()
            text = listener.listen()
            # Mode conversation : après une réponse, on peut enchaîner sans redire « Hey Jarvis ».
            while text:
                show_user(text)
                if _EXIT.match(text):
                    say("À bientôt.")
                    return
                if _RESET.search(text):
                    brain.reset()
                    say("C'est noté, on repart de zéro.")
                else:
                    with console.status("[bright_blue]Jarvis réfléchit...[/]"):
                        reply = _handle(brain, text, say)
                    say(reply)
                text = listener.listen(start_timeout=config.follow_up_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()
        memory.close()


def _beep() -> None:
    try:
        import numpy as np
        import sounddevice as sd

        t = np.linspace(0, 0.12, int(24000 * 0.12), endpoint=False)
        tone = 0.2 * np.sin(2 * np.pi * 880 * t) * np.hanning(t.size)
        sd.play(tone.astype(np.float32), samplerate=24000)
        sd.wait()
    except Exception:
        sys.stdout.write("\a")
