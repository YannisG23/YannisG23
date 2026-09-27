"""Point d'entrée : python -m jarvis [--text] [--doctor] [--no-dashboard] [--no-browser] [--setup-google] [--memory]."""

from __future__ import annotations

import argparse

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Ton assistant personnel J.A.R.V.I.S.")
    parser.add_argument("--text", action="store_true", help="sans micro ni voix (clavier et centre de commande)")
    parser.add_argument("--no-dashboard", action="store_true", help="ne pas lancer le centre de commande")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur au démarrage")
    parser.add_argument("--setup-google", action="store_true", help="connecter Gmail et Google Agenda")
    parser.add_argument("--memory", action="store_true", help="afficher ce que Jarvis sait de toi")
    parser.add_argument("--doctor", action="store_true", help="vérifier l'installation (clé, micro, voix...)")
    args = parser.parse_args()
    config = Config()

    if args.doctor:
        from .doctor import run_doctor

        raise SystemExit(run_doctor(config))
    if args.setup_google:
        from .tools.google_tools import authorize

        authorize(config, interactive=True)
        print(f"Gmail et Agenda connectés. Jeton enregistré dans {config.google_token}")
        return
    if args.memory:
        from .memory import Memory

        memory = Memory(config.memory_db)
        facts = memory.all_facts(limit=100_000)
        print("\n".join(f.render() for f in facts) or "Aucun fait mémorisé pour l'instant.")
        episodes = memory.recent_episodes(limit=20)
        if episodes:
            print("\nConversations récentes :\n" + "\n".join(e.render() for e in episodes))
        return

    from .app import run

    run(config, voice=not args.text, dashboard=not args.no_dashboard, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
