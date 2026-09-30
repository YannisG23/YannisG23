"""Gestion de fichiers."""

from __future__ import annotations

import fnmatch
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from .registry import registry

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", "Library", ".cache"}


# Ouvrir ces fichiers reviendrait à exécuter du code sans confirmation.
_EXECUTABLE_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
                   ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".jar", ".cpl", ".sh", ".command", ".app",
                   ".desktop", ".pif", ".msc", ".py", ".pyw"}


def _resolve(path: str) -> Path:
    return Path(os.path.expandvars(path)).expanduser().resolve()


@registry.tool(
    "Liste le contenu d'un dossier. Raccourcis acceptés : ~ (dossier perso), ~/Desktop, ~/Documents, ~/Downloads.",
    properties={"path": {"type": "string", "description": "Chemin du dossier. Défaut : ~"}},
)
def list_directory(path: str = "~") -> str:
    folder = _resolve(path)
    if not folder.is_dir():
        return f"Dossier introuvable : {folder}"
    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = [f"Contenu de {folder} ({len(entries)} éléments) :"]
    for entry in entries[:200]:
        if entry.is_dir():
            lines.append(f"[dossier] {entry.name}/")
        else:
            try:
                stat = entry.stat()
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                lines.append(f"{entry.name} ({stat.st_size / 1024:.0f} Ko, modifié {modified})")
            except OSError:
                lines.append(entry.name)
    if len(entries) > 200:
        lines.append(f"… et {len(entries) - 200} autres")
    return "\n".join(lines)


@registry.tool(
    "Cherche des fichiers par nom (motif type *.pdf, *facture*, rapport*.docx) dans un dossier et ses sous-dossiers.",
    properties={
        "pattern": {"type": "string", "description": "Motif de nom, insensible à la casse."},
        "root": {"type": "string", "description": "Dossier de départ. Défaut : ~"},
        "limit": {"type": "integer", "description": "Nombre max de résultats. Défaut 30."},
    },
    required=["pattern"],
)
def search_files(pattern: str, root: str = "~", limit: int = 30) -> str:
    base = _resolve(root)
    if not base.is_dir():
        return f"Dossier introuvable : {base}"
    if not any(ch in pattern for ch in "*?["):
        pattern = f"*{pattern}*"
    pattern = pattern.lower()
    matches: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if fnmatch.fnmatch(filename.lower(), pattern):
                matches.append(Path(dirpath) / filename)
                if len(matches) >= limit:
                    break
        if len(matches) >= limit:
            break
    if not matches:
        return f"Aucun fichier « {pattern} » trouvé dans {base}."
    return "\n".join(str(m) for m in matches)


@registry.tool(
    "Lit un fichier texte (txt, md, csv, code, json...).",
    properties={
        "path": {"type": "string"},
        "max_chars": {"type": "integer", "description": "Défaut 20000."},
    },
    required=["path"],
)
def read_text_file(path: str, max_chars: int = 20000) -> str:
    file = _resolve(path)
    if not file.is_file():
        return f"Fichier introuvable : {file}"
    text = file.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n… (tronqué, {len(text) - max_chars} caractères de plus)"
    return text


@registry.tool(
    "Écrit (ou remplace) un fichier texte. Nécessite la confirmation de l'utilisateur.",
    properties={
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    required=["path", "content"],
    confirm=lambda args: f"écrire le fichier {args.get('path', '')}",
)
def write_text_file(path: str, content: str) -> str:
    file = _resolve(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    return f"Fichier écrit : {file} ({len(content)} caractères)"


@registry.tool(
    "Ouvre un fichier ou un dossier avec l'application par défaut.",
    properties={"path": {"type": "string"}},
    required=["path"],
)
def open_path(path: str) -> str:
    target = _resolve(path)
    if not target.exists():
        return f"Introuvable : {target}"
    if target.suffix.lower() in _EXECUTABLE_EXT:
        return (f"Je n'ouvre pas {target.name} : c'est un programme ou un script. "
                "Pour le lancer, passe par run_command (avec confirmation).")
    system = platform.system()
    if system == "Windows":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)], start_new_session=True)
    return f"Ouvert : {target}"
