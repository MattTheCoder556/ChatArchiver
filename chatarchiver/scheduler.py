"""Windows Task Scheduler integration — the cron-like 'run every Monday' feature.

Wraps schtasks.exe. We register a task that launches a tiny generated .bat which runs the
headless exporter with pythonw (no console window). Creating a task in the current user's
context doesn't need admin rights.

Windows-only. On other OSes set_schedule raises a clear message (cron/launchd would go
here later).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .sessions import APP_DIR

TASK_NAME = "ChatArchiverExport"
BAT_PATH = APP_DIR / "scheduled_export.bat"

# schtasks day tokens, Monday-first.
DAY_TOKENS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _pythonw() -> str:
    """Prefer pythonw.exe so the scheduled run shows no console window."""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def _script() -> str:
    # headless_export.py sits at the project root (one level above this package).
    return str(Path(__file__).resolve().parent.parent / "headless_export.py")


def _task_command() -> str:
    """The command the scheduled task runs — packaged exe vs. plain Python source."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --export'      # the packaged ChatArchiver.exe
    return f'"{_pythonw()}" "{_script()}"'          # running from source


def _write_bat() -> str:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    BAT_PATH.write_text(f"@echo off\r\n{_task_command()}\r\n", encoding="utf-8")
    return str(BAT_PATH)


def _schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def set_schedule(frequency: str, day_name: str = "Monday", time_hhmm: str = "09:00",
                 interval: int = 1) -> dict:
    """Create/replace the scheduled task.

    frequency: 'Hourly' (every N hours), 'Daily' (every N days at a time), or
    'Weekly' (on a weekday at a time). interval = the N.
    """
    if not _is_windows():
        raise RuntimeError("Automatic scheduling is currently Windows-only.")
    _validate_time(time_hhmm)
    try:
        interval = max(1, int(interval))
    except Exception:
        interval = 1

    bat = _write_bat()
    args = ["/Create", "/TN", TASK_NAME, "/TR", bat, "/ST", time_hhmm, "/F"]
    freq = frequency.lower()
    if freq == "hourly":
        args += ["/SC", "HOURLY", "/MO", str(min(interval, 23))]
    elif freq == "daily":
        args += ["/SC", "DAILY", "/MO", str(min(interval, 365))]
    else:
        token = DAY_TOKENS[DAY_NAMES.index(day_name)] if day_name in DAY_NAMES else "MON"
        args += ["/SC", "WEEKLY", "/MO", str(min(interval, 52)), "/D", token]

    cp = _schtasks(args)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "schtasks failed").strip())
    return status()


def clear_schedule() -> None:
    if not _is_windows():
        return
    _schtasks(["/Delete", "/TN", TASK_NAME, "/F"])


def status() -> dict:
    """Return {'scheduled': bool, ...details}. Details come from schtasks verbose query."""
    if not _is_windows():
        return {"scheduled": False, "note": "Windows-only"}
    cp = _schtasks(["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if cp.returncode != 0:
        return {"scheduled": False}
    info: dict = {"scheduled": True}
    wanted = {"Schedule Type", "Start Time", "Days", "Next Run Time",
              "Last Run Time", "Last Result"}
    for line in cp.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if k in wanted:
                info[k] = v
    return info


def _validate_time(hhmm: str) -> None:
    try:
        h, m = hhmm.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError
    except Exception:
        raise RuntimeError(f"Time must be HH:MM (24-hour), got '{hhmm}'.")
