"""Fenêtre de bureau : ouvre le centre de commande dans une fenêtre native (pywebview), sans barre de navigateur.

Le serveur local reste le moteur ; la fenêtre ne fait qu'afficher son URL (avec le jeton #token=).
F11 (ou l'appel JS `pywebview.api.toggle_fullscreen()`) bascule entre plein écran et fenêtre.
"""

from __future__ import annotations

import sys
from pathlib import Path


class WindowApi:
    """Méthodes appelables depuis la page (window.pywebview.api.*)."""

    def __init__(self) -> None:
        self.window = None

    def toggle_fullscreen(self) -> None:
        if self.window is not None:
            self.window.toggle_fullscreen()

    def set_title(self, title: str) -> None:
        if self.window is not None and title:
            self.window.title = str(title)[:120]


def make_icon(path: Path) -> Path | None:
    """Dessine une petite icône (sphère bleutée) si elle n'existe pas encore."""
    try:
        if path.exists():
            return path
        from PIL import Image, ImageDraw, ImageFilter

        size = 256
        img = Image.new("RGBA", (size, size), (6, 8, 13, 255))
        glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((48, 48, 208, 208), fill=(80, 170, 255, 200))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(22)))
        ImageDraw.Draw(img).ellipse((72, 72, 184, 184), fill=(143, 190, 255, 255))
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
        return path
    except Exception:
        return None


def _set_windows_icon(title: str, icon: Path) -> None:
    """pywebview n'applique l'icône que sous Qt/GTK : sous Windows on la pose à la main."""
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return
    for which, px in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
        handle = user32.LoadImageW(None, str(icon), 1, px, px, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if handle:
            user32.SendMessageW(hwnd, 0x80, which, handle)  # WM_SETICON


def open_window(url: str, title: str, icon_path: Path | None = None, fullscreen: bool = True) -> None:
    """Ouvre la fenêtre et rend la main quand elle est fermée (à appeler depuis le fil principal)."""
    import webview

    api = WindowApi()
    window = webview.create_window(
        title, url, js_api=api, fullscreen=fullscreen, width=1280, height=800, min_size=(420, 520),
        background_color="#06080d", text_select=False,
    )
    api.window = window
    icon = make_icon(icon_path) if icon_path else None

    def on_shown() -> None:
        if icon and sys.platform == "win32":
            try:
                _set_windows_icon(title, icon)
            except Exception:
                pass

    window.events.shown += on_shown
    webview.start()
