"""Point d'entrée : python -m jarvis [--text] [--login] [--doctor] [--no-dashboard] [--no-browser] [--setup-google] [--memory] [--micros]."""

from __future__ import annotations

import argparse

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Ton assistant personnel")
    parser.add_argument("--text", action="store_true", help="sans micro ni voix (clavier et centre de commande)")
    parser.add_argument("--no-dashboard", action="store_true", help="ne pas lancer le centre de commande")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur au démarrage")
    parser.add_argument("--app", action="store_true",
                        help="ouvrir le centre de commande dans une fenêtre de bureau plein écran (F11 pour basculer)")
    parser.add_argument("--setup-google", action="store_true", help="connecter Gmail et Google Agenda")
    parser.add_argument("--memory", action="store_true", help="afficher ce que l'assistant sait de toi")
    parser.add_argument("--login", action="store_true", help="connecter ton compte Claude (abonnement)")
    parser.add_argument("--micros", action="store_true", help="lister les micros et haut-parleurs disponibles")
    parser.add_argument("--trouver-micro", action="store_true",
                        help="tester chaque micro pendant que tu parles et trouver celui qui marche")
    parser.add_argument("--doctor", action="store_true", help="vérifier l'installation (clé, micro, voix...)")
    args = parser.parse_args()
    config = Config()

    if args.login:
        if not config.uses_subscription:
            print("Mode API (JARVIS_BRAIN=api) : pas de compte à connecter, la clé API suffit.")
            return
        from .brain_subscription import login

        raise SystemExit(login())
    if args.trouver_micro:
        from .voice.devices import scan

        print("Parle sans t'arrêter (compte à voix haute, lis un texte…) jusqu'à la fin du test.")
        results = scan(on_progress=lambda d: print(f"  test de {d['index']:>3}  {d['name']}…", flush=True))
        print("\nRésultats (niveau : > 250 très bien, 30-250 faible, < 30 ne capte rien) :")
        for index, name, level in results:
            shown = "inutilisable" if level < 0 else f"{level:.0f}"
            print(f"  {index:>3}  {shown:>12}  {name}")
        best = results[0] if results and results[0][2] >= 30 else None
        if best:
            print(f"\nLe meilleur : {best[1]}\nMets dans .env : JARVIS_MIC={best[0]}")
        else:
            print("\nAucun micro n'a capté ta voix : vérifie que le micro du casque n'est pas coupé "
                  "(bouton du casque, SteelSeries GG / Sonar, Paramètres Windows > Son > Entrée).")
        return
    if args.micros:
        from .voice.devices import describe

        print(describe())
        return
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

    run(config, voice=not args.text, dashboard=not args.no_dashboard, open_browser=not args.no_browser,
        window=args.app)


if __name__ == "__main__":
    main()
