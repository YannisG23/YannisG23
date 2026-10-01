"""Codex (l'agent de code d'OpenAI) comme second cerveau : deuxième avis et tâches de code.

Il tourne avec l'abonnement ChatGPT de l'utilisateur : ce qu'on lui confie ne consomme pas
le quota Claude. Installation : connexion-codex.bat (npm install -g @openai/codex, puis codex login).
Sans Codex installé, les outils le disent simplement.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .registry import registry

TIMEOUT = 300
MISSING = ("Codex n'est pas installé sur ce PC. Lance connexion-codex.bat dans le dossier de Jarvis "
           "(il faut un abonnement ChatGPT), puis redemande-moi.")
# Caractères que l'interpréteur de commandes Windows pourrait interpréter dans un chemin.
_UNSAFE = set('"&|<>^%\r\n`')


def find_codex() -> str | None:
    found = shutil.which("codex")
    if found:
        return found
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidate = Path(appdata) / "npm" / "codex.cmd"
        if candidate.exists():
            return str(candidate)
    return None


def _folder(folder: str) -> Path | str:
    """Dossier de travail de Codex, ou un message d'erreur."""
    if not folder:
        return Path.home()
    if any(ch in _UNSAFE for ch in folder):
        return "Chemin de dossier invalide."
    path = Path(os.path.expandvars(folder)).expanduser().resolve()
    if not path.is_dir():
        return f"Dossier introuvable : {path}"
    return path


class CodexError(RuntimeError):
    """Codex n'a pas pu répondre ; le message peut être dit tel quel."""


def run_codex(prompt: str, folder: str = "", write: bool = False, timeout: int = TIMEOUT) -> str:
    try:
        return exec_codex(prompt, folder, write, timeout)
    except CodexError as exc:
        return str(exc)


def exec_codex(prompt: str, folder: str = "", write: bool = False, timeout: int = TIMEOUT) -> str:
    """Lance Codex ; lève CodexError en cas d'échec (utilisé aussi par le relais du cerveau)."""
    executable = find_codex()
    if not executable:
        raise CodexError(MISSING)
    cwd = _folder(folder)
    if isinstance(cwd, str):
        raise CodexError(cwd)
    with tempfile.TemporaryDirectory() as tmp:
        answer_file = Path(tmp) / "reponse.txt"
        # La demande passe par l'entrée standard (« - ») : jamais dans la ligne de commande.
        command = [executable, "exec", "--skip-git-repo-check",
                   "--sandbox", "workspace-write" if write else "read-only",
                   "--cd", str(cwd), "--output-last-message", str(answer_file), "-"]
        # Sans la clé API de la voix OpenAI : Codex doit rester sur l'abonnement ChatGPT, pas facturer l'API.
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        try:
            result = subprocess.run(command, input=prompt, capture_output=True, text=True, env=env,
                                    encoding="utf-8", errors="replace", timeout=timeout, cwd=str(cwd))
        except subprocess.TimeoutExpired:
            raise CodexError(f"Codex n'a pas fini en {max(timeout // 60, 1)} minutes : j'ai arrêté.")
        except OSError as exc:
            raise CodexError(f"Impossible de lancer Codex : {exc}")
        answer = answer_file.read_text(encoding="utf-8", errors="replace").strip() if answer_file.exists() else ""
    if result.returncode != 0 and not answer:
        detail = (result.stderr or result.stdout or "").strip()[-1500:]
        if "login" in detail.lower() or "auth" in detail.lower():
            raise CodexError("Codex n'est pas connecté à ton compte ChatGPT : lance connexion-codex.bat.")
        raise CodexError(f"Codex a échoué (code {result.returncode}) : {detail or 'aucun détail'}")
    from .. import usage

    if usage.current is not None:
        usage.current.record("codex")
    result_text = answer or (result.stdout or "").strip()[-6000:]
    if not result_text:
        raise CodexError("Codex n'a rien répondu.")
    return result_text


@registry.tool(
    "Demande à Codex (l'IA de code d'OpenAI, sur l'abonnement ChatGPT de l'utilisateur : ça ne consomme pas le "
    "quota Claude). Lecture seule, il ne modifie rien. À utiliser : (1) quand l'utilisateur dit « demande à "
    "ChatGPT / à Codex » ou veut un « deuxième avis » ; (2) de toi-même pour les grosses tâches de code : "
    "analyse ou explication d'un projet, relecture de code, recherche de bug sur plusieurs fichiers, avis "
    "d'architecture. Pas pour la conversation, la mémoire ni les actions sur le PC. Codex ne connaît pas la "
    "conversation : mets tout le contexte utile dans la question. Ça prend une à deux minutes : préviens "
    "l'utilisateur avant.",
    properties={
        "question": {"type": "string", "description": "La demande complète, avec tout le contexte utile."},
        "folder": {"type": "string", "description": "Dossier du projet à examiner (facultatif)."},
    },
    required=["question"],
)
def ask_codex(question: str, folder: str = "") -> str:
    return run_codex(question, folder, write=False)


@registry.tool(
    "Confie à Codex (abonnement ChatGPT, sans consommer le quota Claude) une grosse tâche de code qui modifie "
    "des fichiers dans un dossier : créer un script ou un projet, corriger un bug, ajouter une fonction, "
    "refactorer. Préfère-le à un travail long de ta part sur du code. Il ne peut écrire que dans ce dossier. "
    "Nécessite la confirmation de l'utilisateur. Résume ensuite ce qu'il a changé.",
    properties={
        "task": {"type": "string", "description": "Ce qu'il faut faire, précisément."},
        "folder": {"type": "string", "description": "Dossier du projet où il a le droit d'écrire."},
    },
    required=["task", "folder"],
    confirm=lambda args: f"laisser Codex modifier des fichiers dans {args.get('folder', '?')} : {args.get('task', '')}",
)
def codex_task(task: str, folder: str) -> str:
    return run_codex(task, folder, write=True, timeout=600)
