"""Point d'entrée : python -m jarvis [--text | --setup-google | --memory]."""

from __future__ import annotations

import argparse

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Ton assistant personnel J.A.R.V.I.S.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="discuter au clavier plutôt qu'à la voix")
    mode.add_argument("--setup-google", action="store_true", help="connecter Gmail et Google Agenda")
    mode.add_argument("--memory", action="store_true", help="afficher ce que Jarvis sait de toi")
    args = parser.parse_args()
    config = Config()

    if args.setup_google:
        from .tools.google_tools import authorize

        authorize(config, interactive=True)
        print(f"Gmail et Agenda connectés. Jeton enregistré dans {config.google_token}")
    elif args.memory:
        from .memory import Memory

        facts = Memory(config.memory_db).all_facts(limit=10_000)
        print("\n".join(f.render() for f in facts) or "Mémoire vide pour l'instant.")
    elif args.text:
        from .app import run_text

        run_text(config)
    else:
        from .app import run_voice

        run_voice(config)


if __name__ == "__main__":
    main()
