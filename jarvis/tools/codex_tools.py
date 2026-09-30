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


def run_codex(prompt: str, folder: str = "", write: bool = False, timeout: int = TIMEOUT) -> str:
    executable = find_codex()
    if not executable:
        return MISSING
    cwd = _folder(folder)
    if isinstance(cwd, str):
        return cwd
    with tempfile.TemporaryDirectory() as tmp:
        answer_file = Path(tmp) / "reponse.txt"
        # La demande passe par l'entrée standard (« - ») : jamais dans la ligne de commande.
        command = [executable, "exec", "--skip-git-repo-check",
                   "--sandbox", "workspace-write" if write else "read-only",
                   "--cd", str(cwd), "--output-last-message", str(answer_file), "-"]
        try:
            result = subprocess.run(command, input=prompt, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout, cwd=str(cwd))
        except subprocess.TimeoutExpired:
            return f"Codex n'a pas fini en {timeout // 60} minutes : j'ai arrêté."
        except OSError as exc:
            return f"Impossible de lancer Codex : {exc}"
        answer = answer_file.read_text(encoding="utf-8", errors="replace").strip() if answer_file.exists() else ""
    if result.returncode != 0 and not answer:
        detail = (result.stderr or result.stdout or "").strip()[-1500:]
        if "login" in detail.lower() or "auth" in detail.lower():
            return "Codex n'est pas connecté à ton compte ChatGPT : lance connexion-codex.bat."
        return f"Codex a échoué (code {result.returncode}) : {detail or 'aucun détail'}"
    return answer or (result.stdout or "").strip()[-6000:] or "Codex n'a rien répondu."


@registry.tool(
    "Demande à Codex (l'IA de code d'OpenAI, abonnement ChatGPT de l'utilisateur) : deuxième avis sur une "
    "question ou une décision, analyse ou explication de code, recherche de bug. Lecture seule : il ne modifie "
    "rien. À utiliser quand l'utilisateur demande l'avis de Codex (ou de ChatGPT), ou pour une grosse analyse "
    "de code, ce qui économise le quota Claude. Ça peut prendre une ou deux minutes : préviens-le.",
    properties={
        "question": {"type": "string", "description": "La demande complète, avec tout le contexte utile."},
        "folder": {"type": "string", "description": "Dossier du projet à examiner (facultatif)."},
    },
    required=["question"],
)
def ask_codex(question: str, folder: str = "") -> str:
    return run_codex(question, folder, write=False)


@registry.tool(
    "Confie à Codex une tâche de code qui modifie des fichiers dans un dossier (créer un script, corriger un "
    "bug, ajouter une fonction). Il ne peut écrire que dans ce dossier. Nécessite la confirmation de "
    "l'utilisateur. Résume ensuite ce qu'il a changé.",
    properties={
        "task": {"type": "string", "description": "Ce qu'il faut faire, précisément."},
        "folder": {"type": "string", "description": "Dossier du projet où il a le droit d'écrire."},
    },
    required=["task", "folder"],
    confirm=lambda args: f"laisser Codex modifier des fichiers dans {args.get('folder', '?')} : {args.get('task', '')}",
)
def codex_task(task: str, folder: str) -> str:
    return run_codex(task, folder, write=True, timeout=600)
