"""Contrôle de l'ordinateur : applications, web, médias, écran, presse-papiers, minuteurs, shell."""

from __future__ import annotations

import base64
import io
import platform
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from .registry import ToolContext, registry

SYSTEM = platform.system()  # "Windows", "Darwin" (macOS) ou "Linux"
_timers: dict[int, tuple[threading.Timer, str, datetime]] = {}
_timer_ids = iter(range(1, 1_000_000))


def _truncate(text: str, limit: int = 6000) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… (tronqué, {len(text) - limit} caractères de plus)"


@registry.tool(
    "Ouvre une application installée sur l'ordinateur (ex. Spotify, Chrome, Discord, Word, "
    "calculatrice, VS Code).",
    properties={"name": {"type": "string", "description": "Nom de l'application."}},
    required=["name"],
)
def open_application(name: str) -> str:
    name = name.strip()
    if SYSTEM == "Darwin":
        result = subprocess.run(["open", "-a", name], capture_output=True, text=True)
        if result.returncode != 0:
            return f"Impossible d'ouvrir « {name} » : {result.stderr.strip()}"
    elif SYSTEM == "Windows":
        # « start » passe par le shell Windows, qui connaît les applis enregistrées.
        subprocess.Popen(f'start "" "{name}"', shell=True)
    else:
        executable = shutil.which(name) or shutil.which(name.lower())
        if not executable:
            return f"Application « {name} » introuvable dans le PATH."
        subprocess.Popen([executable], start_new_session=True)
    return f"J'ai lancé {name}."


@registry.tool(
    "Ouvre une page web dans le navigateur par défaut.",
    properties={"url": {"type": "string", "description": "Adresse complète (https://...)."}},
    required=["url"],
)
def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Page ouverte : {url}"


@registry.tool(
    "Lance de la musique ou une vidéo : ouvre la recherche YouTube (ou YouTube Music) correspondante.",
    properties={
        "query": {"type": "string", "description": "Titre, artiste, playlist, ambiance..."},
        "service": {"type": "string", "enum": ["youtube", "youtube_music"]},
    },
    required=["query"],
)
def play_media(query: str, service: str = "youtube") -> str:
    q = urllib.parse.quote_plus(query)
    url = (
        f"https://music.youtube.com/search?q={q}"
        if service == "youtube_music"
        else f"https://www.youtube.com/results?search_query={q}"
    )
    webbrowser.open(url)
    return f"Recherche « {query} » ouverte sur {service}."


@registry.tool(
    "Contrôle la lecture multimédia et le volume via les touches média du clavier.",
    properties={
        "action": {
            "type": "string",
            "enum": ["play_pause", "next", "previous", "volume_up", "volume_down", "mute"],
        },
        "times": {"type": "integer", "description": "Nombre d'appuis (pour le volume). Défaut 1."},
    },
    required=["action"],
)
def media_control(action: str, times: int = 1) -> str:
    import pyautogui

    keys = {
        "play_pause": "playpause",
        "next": "nexttrack",
        "previous": "prevtrack",
        "volume_up": "volumeup",
        "volume_down": "volumedown",
        "mute": "volumemute",
    }
    if action not in keys:
        return f"Action inconnue : {action}"
    pyautogui.press(keys[action], presses=max(1, min(int(times), 50)))
    return f"Action média effectuée : {action} ×{times}"


@registry.tool("Donne l'état de l'ordinateur : processeur, mémoire, disque, batterie, système.")
def system_status() -> str:
    import psutil

    lines = [
        f"Système : {platform.system()} {platform.release()} ({platform.machine()})",
        f"Processeur : {psutil.cpu_percent(interval=0.5):.0f}% utilisé, {psutil.cpu_count()} cœurs",
    ]
    mem = psutil.virtual_memory()
    lines.append(f"Mémoire : {mem.percent:.0f}% utilisée ({mem.used / 2**30:.1f} / {mem.total / 2**30:.1f} Go)")
    disk = psutil.disk_usage(Path.home().anchor or "/")
    lines.append(f"Disque : {disk.percent:.0f}% utilisé ({disk.free / 2**30:.0f} Go libres)")
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery:
        state = "en charge" if battery.power_plugged else "sur batterie"
        lines.append(f"Batterie : {battery.percent:.0f}% ({state})")
    return "\n".join(lines)


@registry.tool(
    "Regarde l'écran de l'utilisateur (capture d'écran) pour voir ce qu'il affiche, "
    "lire un message d'erreur, décrire une page, etc.",
)
def look_at_screen() -> list[dict]:
    import pyautogui

    image = pyautogui.screenshot()
    image.thumbnail((1568, 1568))  # taille max utile pour la vision de Claude
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(buffer.getvalue()).decode(),
            },
        },
        {"type": "text", "text": "Capture de l'écran principal."},
    ]


@registry.tool("Lit le contenu texte du presse-papiers.")
def read_clipboard() -> str:
    import pyperclip

    text = pyperclip.paste()
    return _truncate(text) if text else "Le presse-papiers est vide."


@registry.tool(
    "Copie un texte dans le presse-papiers.",
    properties={"text": {"type": "string"}},
    required=["text"],
)
def write_clipboard(text: str) -> str:
    import pyperclip

    pyperclip.copy(text)
    return "Texte copié dans le presse-papiers."


@registry.tool(
    "Programme un minuteur ou un rappel. Jarvis préviendra l'utilisateur à voix haute.",
    properties={
        "seconds": {"type": "integer", "description": "Délai en secondes."},
        "label": {"type": "string", "description": "Ce qu'il faudra rappeler."},
    },
    required=["seconds", "label"],
)
def set_timer(ctx: ToolContext, seconds: int, label: str) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "Le délai doit être positif."
    timer_id = next(_timer_ids)

    def ring() -> None:
        _timers.pop(timer_id, None)
        ctx.notify(f"Rappel : {label}")

    timer = threading.Timer(seconds, ring)
    timer.daemon = True
    timer.start()
    due = datetime.now() + timedelta(seconds=seconds)
    _timers[timer_id] = (timer, label, due)
    return f"Minuteur #{timer_id} réglé pour {due:%H:%M:%S} : {label}"


@registry.tool("Liste les minuteurs et rappels en cours.")
def list_timers() -> str:
    if not _timers:
        return "Aucun minuteur en cours."
    return "\n".join(f"#{i} à {due:%H:%M:%S} : {label}" for i, (_, label, due) in sorted(_timers.items()))


@registry.tool(
    "Annule un minuteur en cours.",
    properties={"timer_id": {"type": "integer"}},
    required=["timer_id"],
)
def cancel_timer(timer_id: int) -> str:
    entry = _timers.pop(int(timer_id), None)
    if not entry:
        return f"Aucun minuteur #{timer_id}."
    entry[0].cancel()
    return f"Minuteur #{timer_id} annulé."


@registry.tool(
    "Exécute une commande dans le terminal de l'ordinateur (PowerShell/cmd sous Windows, "
    "shell sous macOS/Linux) et renvoie sa sortie. Nécessite la confirmation de l'utilisateur.",
    properties={
        "command": {"type": "string", "description": "La commande à exécuter."},
        "reason": {"type": "string", "description": "Pourquoi, en une phrase courte."},
    },
    required=["command"],
    confirm=lambda args: f"exécuter la commande : {args.get('command', '')}",
)
def run_command(command: str, reason: str = "") -> str:
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "La commande a dépassé 120 secondes et a été arrêtée."
    output = (result.stdout or "") + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
    return _truncate(f"Code de sortie : {result.returncode}\n{output.strip()}")
