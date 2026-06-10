"""System-tray icon so the app keeps running with its window closed.

Closing the window hides it (withdraw) instead of quitting; a tray icon stays in the
notification area / 'hidden icons' tray on Windows and the AppIndicator/status area on
Linux. Its menu reopens the window, runs an export on demand, or really quits.

Built on pystray. Tkinter must own the main thread, so the tray icon runs its own loop on
a background thread (run_detached) and every menu action is marshalled back onto the tk
thread via root.after — never touch tk widgets from the tray thread directly.

If pystray (or a platform backend) is unavailable, build_tray() returns None and the
caller falls back to plain quit-on-close. The app still works; it just won't live in the
tray on that machine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _icon_image():
    """Load the app logo as a PIL image for the tray, or synthesise a simple fallback."""
    from PIL import Image
    base = getattr(sys, "_MEIPASS", None)
    if base:
        png = os.path.join(base, "chatarchiver", "assets", "logos", "app.png")
    else:
        png = os.path.join(os.path.dirname(__file__), "assets", "logos", "app.png")
    if Path(png).exists():
        return Image.open(png).convert("RGBA")
    img = Image.new("RGBA", (64, 64), (79, 70, 229, 255))   # indigo square fallback
    return img


def build_tray(*, on_open, on_export_now, on_quit, schedule_text):
    """Create (but don't start) a tray icon.

    on_open / on_export_now / on_quit: zero-arg callables (each should marshal its own work
        onto the tk thread). schedule_text: zero-arg callable returning the current
        schedule line shown as a disabled menu item.
    Returns the pystray.Icon, or None if a tray can't be created here.
    """
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception:
        return None

    def _schedule_item(_icon, _item):
        return schedule_text()

    menu = Menu(
        MenuItem("Open Chat Archiver", lambda: on_open(), default=True),
        MenuItem("Run export now", lambda: on_export_now()),
        Menu.SEPARATOR,
        MenuItem(_schedule_item, None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Quit Chat Archiver", lambda: on_quit()),
    )
    try:
        icon = pystray.Icon("chatarchiver", _icon_image(), "Chat Archiver", menu)
    except Exception:
        return None
    return icon
