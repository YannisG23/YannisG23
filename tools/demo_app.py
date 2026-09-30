"""Démo du centre de commande avec un Core factice (aucun appel à Claude, ~/.jarvis intact).

    python tools/demo_app.py [port]     # port 8799 par défaut

Affiche l'URL avec jeton ; POST /demo/<état> n'existe pas : on pilote l'état par le bus d'événements
depuis un fil qui fait défiler repos → écoute → réflexion → parole, ou depuis Playwright via
/api/ask (qui publie un état « thinking » puis « speaking »).
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JARVIS_HOME"] = tempfile.mkdtemp(prefix="jarvis-demo-")
os.environ.setdefault("JARVIS_USER_NAME", "Yannis")
os.environ.setdefault("JARVIS_CITY", "")

from jarvis.config import Config  # noqa: E402
from jarvis.core import Core  # noqa: E402
from jarvis.dashboard.server import Dashboard  # noqa: E402
from jarvis.memory import Memory  # noqa: E402


class _NoClient:
    def __getattr__(self, name):
        raise RuntimeError("démo : pas de cerveau")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    config = Config()
    memory = Memory(config.memory_db)
    memory.remember("Aime le café serré le matin", importance=2)
    memory.remember("Travaille sur Jarvis, son assistant vocal", importance=3)
    core = Core(config, memory=memory, client=_NoClient())
    board = Dashboard(core, port)
    board.start()
    print(board.url, flush=True)

    def set_state(state: str, seconds: float, levels: bool = False) -> None:
        core.set_state(state)
        end = time.time() + seconds
        while time.time() < end:
            v = 0.35 + 0.3 * math.sin(time.time() * 9) + 0.2 * math.sin(time.time() * 23)
            if levels:
                core.bus.publish("levels", {"mic": max(0.0, v) if state == "listening" else 0.0,
                                            "out": max(0.0, v) if state == "speaking" else 0.0})
            time.sleep(0.05)

    def loop() -> None:
        time.sleep(3)
        while True:
            core.bus.publish("caption", {"text": ""})
            set_state("idle", 6)
            set_state("listening", 6, True)
            set_state("thinking", 6)
            core.bus.publish("caption", {"text": "Voilà, j'ai réorganisé ta matinée : réunion à 10 h, le reste est libre."})
            set_state("speaking", 8, True)

    if "--cycle" in sys.argv:
        threading.Thread(target=loop, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
