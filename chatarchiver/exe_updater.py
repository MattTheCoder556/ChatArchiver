"""Self-update for the frozen .exe: download newer GitHub Releases and swap in place.

This is the counterpart to updater.py (which git-pulls when running from source). It only
does anything in a PyInstaller one-folder build (sys.frozen). The flow:

  check()              ask the public Releases API for the latest version, compare to the
                       version baked into this exe (chatarchiver._version).
  download_and_stage() if newer, download the release .zip and extract it to a temp dir.
  apply()              spawn a tiny detached helper that waits for THIS process to exit,
                       mirrors the new files over the install folder, and relaunches.

Windows can't overwrite a running .exe, which is why the swap is done by an external
helper after the app quits. Everything is best-effort and stdlib-only (no extra deps):
any network/API/permission failure just leaves the current build in place.

User data (config, profiles, manifests) lives under ~/.chatarchiver, NOT the install
folder — so mirroring the install folder never touches it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ._version import __version__ as CURRENT

REPO = "MattTheCoder556/ChatArchiver"
_UA = f"ChatArchiver/{CURRENT} (auto-updater)"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # run helper without a flashing window

# Helper batch: %1=pid to wait for, %2=new build dir, %3=install dir.
# Waits for the app to exit, mirrors new->install (robocopy retries on locked files),
# relaunches, then deletes itself. ping is the delay (timeout needs an interactive console).
_HELPER = r"""@echo off
:wait
tasklist /FI "PID eq %~1" | find "%~1" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
robocopy "%~2" "%~3" /MIR /R:20 /W:1 /NFL /NDL /NJH /NJS >nul
start "" "%~3\ChatArchiver.exe"
del "%~f0"
"""


def is_frozen() -> bool:
    """True only in a PyInstaller build — the only place a download-swap makes sense."""
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    return CURRENT


def install_dir() -> Path:
    """The folder holding the running ChatArchiver.exe (one-folder build)."""
    return Path(sys.executable).resolve().parent


def _vt(s: str) -> tuple:
    """Loose version tuple: 'v0.1.12' -> (0, 1, 12). Numeric parts only, for comparison."""
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums) or (0,)


def _api_latest() -> dict:
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def check() -> tuple[bool, str, str]:
    """Return (update_available, latest_version, zip_url). Best-effort: any failure or a
    non-newer release yields (False, current_version, '')."""
    try:
        data = _api_latest()
    except Exception:
        return (False, CURRENT, "")
    tag = (data.get("tag_name") or "").lstrip("v")
    zip_url = next((a.get("browser_download_url", "")
                    for a in data.get("assets", [])
                    if (a.get("name") or "").lower().endswith(".zip")), "")
    if not tag or not zip_url:
        return (False, CURRENT, "")
    return (_vt(tag) > _vt(CURRENT), tag, zip_url)


def download_and_stage(zip_url: str, log=print) -> Path:
    """Download the release zip and extract it; return the path to the new build folder
    (the directory containing ChatArchiver.exe). Raises on failure."""
    tmp = Path(tempfile.mkdtemp(prefix="chatarchiver_upd_"))
    zpath = tmp / "build.zip"
    log("[update] downloading new build…")
    req = urllib.request.Request(zip_url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(zpath, "wb") as f:
        shutil.copyfileobj(r, f)
    log("[update] extracting…")
    dest = tmp / "x"
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest)
    exe = dest / "ChatArchiver" / "ChatArchiver.exe"
    if exe.exists():
        return exe.parent
    found = list(dest.rglob("ChatArchiver.exe"))    # tolerate a differently-nested zip
    if not found:
        raise RuntimeError("ChatArchiver.exe not found inside the downloaded release zip")
    return found[0].parent


def apply(new_dir: Path, log=print) -> None:
    """Launch the detached swap helper and return. The CALLER must then exit the app
    (e.g. root.destroy()) so the helper can overwrite the now-unlocked exe and relaunch."""
    helper = Path(tempfile.gettempdir()) / f"chatarchiver_update_{os.getpid()}.bat"
    helper.write_text(_HELPER, encoding="utf-8")
    log("[update] installing — the app will close and reopen on the new version…")
    subprocess.Popen(["cmd", "/c", str(helper), str(os.getpid()),
                      str(new_dir), str(install_dir())],
                     creationflags=_NO_WINDOW, close_fds=True)
