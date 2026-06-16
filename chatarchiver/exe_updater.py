"""Self-update for the frozen .exe: download newer GitHub Releases and swap in place.

This is the counterpart to updater.py (which git-pulls when running from source). It only
does anything in a PyInstaller one-folder build (sys.frozen). The flow:

  check()              ask the public Releases API for the latest version, compare to the
                       version baked into this build (chatarchiver._version).
  download_and_stage() if newer, download this platform's release asset (.zip on Windows,
                       .tar.gz on Linux) and extract it to a temp dir.
  apply()              spawn a tiny detached helper that waits for THIS process to exit,
                       mirrors the new files over the install folder, and relaunches.

The swap is done by an external helper after the app quits (a .bat on Windows, a /bin/sh
script on Linux) — Windows can't overwrite a running .exe, and relaunching cleanly is
simplest once the old process is gone. Everything is best-effort and stdlib-only: any
network/API/permission failure just leaves the current build in place.

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
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ._version import __version__ as CURRENT

REPO = "MattTheCoder556/ChatArchiver"
_UA = f"ChatArchiver/{CURRENT} (auto-updater)"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # run helper without a flashing window

_IS_WIN = sys.platform.startswith("win")
# Per-OS release asset + the executable name inside the one-folder build.
_ASSET_SUFFIX = ".zip" if _IS_WIN else ".tar.gz"
_EXE_NAME = "ChatArchiver.exe" if _IS_WIN else "ChatArchiver"

# Windows helper batch: %1=pid to wait for, %2=new build dir, %3=install dir.
# Waits for the app to exit, mirrors new->install (robocopy retries on locked files),
# relaunches, then deletes itself. ping is the delay (timeout needs an interactive console).
_HELPER_WIN = r"""@echo off
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

# POSIX helper: $1=pid to wait for, $2=new build dir, $3=install dir. Waits for the app
# to exit, mirrors new->install (rsync --delete, or cp -a as a fallback), relaunches, and
# removes itself. On Linux a running binary's file can be replaced freely.
_HELPER_NIX = r"""#!/bin/sh
while kill -0 "$1" 2>/dev/null; do sleep 0.5; done
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$2"/ "$3"/
else
  cp -a "$2"/. "$3"/
fi
chmod +x "$3/ChatArchiver" 2>/dev/null
"$3/ChatArchiver" >/dev/null 2>&1 &
rm -f "$0"
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
    """Return (update_available, latest_version, asset_url) for THIS platform's build.
    Best-effort: any failure or a non-newer release yields (False, current_version, '')."""
    try:
        data = _api_latest()
    except Exception:
        return (False, CURRENT, "")
    tag = (data.get("tag_name") or "").lstrip("v")
    url = next((a.get("browser_download_url", "")     # .zip on Windows, .tar.gz on Linux
                for a in data.get("assets", [])
                if (a.get("name") or "").lower().endswith(_ASSET_SUFFIX)), "")
    if not tag or not url:
        return (False, CURRENT, "")
    return (_vt(tag) > _vt(CURRENT), tag, url)


def download_and_stage(asset_url: str, log=print) -> Path:
    """Download the release archive (.zip on Windows, .tar.gz on Linux) and extract it;
    return the path to the new build folder (the dir holding the executable). Raises on
    failure."""
    tmp = Path(tempfile.mkdtemp(prefix="chatarchiver_upd_"))
    arch = tmp / ("build.zip" if _IS_WIN else "build.tar.gz")
    log("[update] downloading new build…")
    req = urllib.request.Request(asset_url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(arch, "wb") as f:
        shutil.copyfileobj(r, f)
    log("[update] extracting…")
    dest = tmp / "x"
    if _IS_WIN:
        with zipfile.ZipFile(arch) as z:
            z.extractall(dest)
    else:
        with tarfile.open(arch) as t:
            t.extractall(dest)
    exe = dest / "ChatArchiver" / _EXE_NAME
    if exe.exists():
        return exe.parent
    found = list(dest.rglob(_EXE_NAME))             # tolerate a differently-nested archive
    if not found:
        raise RuntimeError(f"{_EXE_NAME} not found inside the downloaded release archive")
    return found[0].parent


def apply(new_dir: Path, log=print) -> None:
    """Launch the detached swap helper and return. The CALLER must then exit the app
    (e.g. root.destroy()) so the helper can replace the install folder and relaunch."""
    log("[update] installing — the app will close and reopen on the new version…")
    pid, new, inst = str(os.getpid()), str(new_dir), str(install_dir())
    tmpdir = Path(tempfile.gettempdir())
    if _IS_WIN:
        helper = tmpdir / f"chatarchiver_update_{os.getpid()}.bat"
        helper.write_text(_HELPER_WIN, encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(helper), pid, new, inst],
                         creationflags=_NO_WINDOW, close_fds=True)
    else:
        helper = tmpdir / f"chatarchiver_update_{os.getpid()}.sh"
        helper.write_text(_HELPER_NIX, encoding="utf-8")
        os.chmod(helper, 0o755)
        subprocess.Popen(["/bin/sh", str(helper), pid, new, inst],
                         start_new_session=True, close_fds=True)  # survive our exit
