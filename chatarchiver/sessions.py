"""Where the app keeps its per-provider browser profiles, config and default output."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Everything the app stores lives under the user's home in a hidden folder.
APP_DIR = Path.home() / ".chatarchiver"
PROFILES_DIR = APP_DIR / "profiles"
LOGS_DIR = APP_DIR / "logs"
CONFIG_PATH = APP_DIR / "config.json"


def browsers_cache_dir() -> Path:
    """Playwright's default per-user browser cache for this OS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def ensure_browsers_path() -> None:
    """Point Playwright at the per-user browser cache before any browser launch.

    A PyInstaller-frozen build otherwise resolves the browser path relative to the bundle
    (where no browser exists) instead of the user cache, so a launch dies with
    'Executable doesn't exist'. Setting PLAYWRIGHT_BROWSERS_PATH (without clobbering an
    explicit user value) makes the packaged app find the same Firefox that
    `playwright install firefox` downloaded.
    """
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers_cache_dir()))


def profile_dir(provider_id: str) -> Path:
    """Persistent Firefox profile dir for one provider — this is the saved login."""
    d = PROFILES_DIR / provider_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def has_profile(provider_id: str) -> bool:
    """True if this provider was ever connected (a saved login exists on disk)."""
    return (PROFILES_DIR / provider_id).exists()


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "Chat Archive"


# ---- app config (output dir, schedule) — shared by the GUI and the headless run ----

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def output_dir_from_config() -> Path:
    val = load_config().get("output_dir")
    return Path(val) if val else default_output_dir()
