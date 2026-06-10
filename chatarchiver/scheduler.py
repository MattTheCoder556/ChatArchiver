"""Cron-like 'run the export in the background' integration — cross-platform.

Two OS backends behind one API (set_schedule / clear_schedule / status):

  • Windows -> Task Scheduler (schtasks.exe). Registers a task that runs a tiny generated
    .bat which launches the headless exporter with pythonw (no console window).
  • Linux   -> a systemd *user* timer (~/.config/systemd/user/chatarchiver-export.{service,
    timer}). Survives reboot (Persistent=true) and runs whether or not the app window is
    open. Needs systemd, which every mainstream desktop distro ships.

Both register the SAME command — the packaged exe with --export, or `python headless_
export.py` from source — so the unattended run is identical across platforms.

macOS isn't wired yet (launchd would go here); set_schedule raises a clear message there.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .sessions import APP_DIR

TASK_NAME = "ChatArchiverExport"
BAT_PATH = APP_DIR / "scheduled_export.bat"

# Monday-first weekday tables, shared by both backends (the UI offers these names).
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WIN_DAY_TOKENS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_SYSTEMD_DAY_TOKENS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# ---- the command both backends run ---------------------------------------------------

def _pythonw() -> str:
    """Prefer pythonw.exe (Windows) so the scheduled run shows no console window."""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def _script() -> str:
    # headless_export.py sits at the project root (one level above this package).
    return str(Path(__file__).resolve().parent.parent / "headless_export.py")


# ====================================================================================
# Public API
# ====================================================================================

def set_schedule(frequency: str, day_name: str = "Monday", time_hhmm: str = "09:00",
                 interval: int = 1) -> dict:
    """Create/replace the background export schedule.

    frequency: 'Hourly' (every N hours), 'Daily' (every N days at a time), or
    'Weekly' (on a weekday at a time). interval = the N.
    """
    _validate_time(time_hhmm)
    try:
        interval = max(1, int(interval))
    except Exception:
        interval = 1

    if _is_windows():
        return _win_set(frequency, day_name, time_hhmm, interval)
    if _is_linux():
        return _systemd_set(frequency, day_name, time_hhmm, interval)
    raise RuntimeError("Automatic scheduling isn't supported on this OS yet.")


def clear_schedule() -> None:
    if _is_windows():
        _win_clear()
    elif _is_linux():
        _systemd_clear()


def status() -> dict:
    """Return {'scheduled': bool, ...details}. Details vary by backend but always include
    'Next Run Time' when available."""
    if _is_windows():
        return _win_status()
    if _is_linux():
        return _systemd_status()
    return {"scheduled": False, "note": "unsupported OS"}


# ====================================================================================
# Windows backend (schtasks)
# ====================================================================================

def _win_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --export'      # the packaged ChatArchiver.exe
    return f'"{_pythonw()}" "{_script()}"'          # running from source


def _win_write_bat() -> str:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    BAT_PATH.write_text(f"@echo off\r\n{_win_command()}\r\n", encoding="utf-8")
    return str(BAT_PATH)


def _schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _win_set(frequency: str, day_name: str, time_hhmm: str, interval: int) -> dict:
    bat = _win_write_bat()
    args = ["/Create", "/TN", TASK_NAME, "/TR", bat, "/ST", time_hhmm, "/F"]
    freq = frequency.lower()
    if freq == "hourly":
        args += ["/SC", "HOURLY", "/MO", str(min(interval, 23))]
    elif freq == "daily":
        args += ["/SC", "DAILY", "/MO", str(min(interval, 365))]
    else:
        token = _WIN_DAY_TOKENS[DAY_NAMES.index(day_name)] if day_name in DAY_NAMES else "MON"
        args += ["/SC", "WEEKLY", "/MO", str(min(interval, 52)), "/D", token]

    cp = _schtasks(args)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "schtasks failed").strip())
    return _win_status()


def _win_clear() -> None:
    _schtasks(["/Delete", "/TN", TASK_NAME, "/F"])


def _win_status() -> dict:
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


# ====================================================================================
# Linux backend (systemd user timer)
# ====================================================================================

UNIT_NAME = "chatarchiver-export"
_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
_SERVICE_PATH = _SYSTEMD_USER_DIR / f"{UNIT_NAME}.service"
_TIMER_PATH = _SYSTEMD_USER_DIR / f"{UNIT_NAME}.timer"


def _systemctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _require_systemd() -> None:
    cp = subprocess.run(["systemctl", "--user", "--version"],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(
            "Automatic scheduling on Linux needs systemd (systemctl --user). "
            "It wasn't found — schedule the export with cron manually instead.")


def _linux_exec_start() -> str:
    """The ExecStart line for the service unit (absolute paths — systemd has no cwd)."""
    if getattr(sys, "frozen", False):
        return f"{sys.executable} --export"
    return f"{sys.executable} {_script()}"


def _on_calendar(frequency: str, day_name: str, time_hhmm: str, interval: int) -> str:
    """Map our (frequency, interval, day, time) to a systemd OnCalendar expression."""
    h, m = (int(x) for x in time_hhmm.split(":"))
    hhmm = f"{h:02d}:{m:02d}:00"
    freq = frequency.lower()
    if freq == "hourly":
        step = max(1, min(interval, 23))
        return f"*-*-* 0/{step}:00:00" if step > 1 else "*-*-* *:00:00"
    if freq == "daily":
        if interval > 1:
            # 'every N days' has no exact OnCalendar form; day-of-month stepping is the
            # closest (it restarts at the 1st each month — a small drift we accept).
            return f"*-*-1/{min(interval, 28)} {hhmm}"
        return f"*-*-* {hhmm}"
    # weekly: systemd can't express 'every N weeks', so we run on that weekday each week.
    token = _SYSTEMD_DAY_TOKENS[DAY_NAMES.index(day_name)] if day_name in DAY_NAMES else "Mon"
    return f"{token} *-*-* {hhmm}"


def _systemd_set(frequency: str, day_name: str, time_hhmm: str, interval: int) -> dict:
    _require_systemd()
    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    _SERVICE_PATH.write_text(
        "[Unit]\n"
        "Description=Chat Archiver — unattended incremental export\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_linux_exec_start()}\n",
        encoding="utf-8",
    )
    _TIMER_PATH.write_text(
        "[Unit]\n"
        "Description=Chat Archiver export schedule\n\n"
        "[Timer]\n"
        f"OnCalendar={_on_calendar(frequency, day_name, time_hhmm, interval)}\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n",
        encoding="utf-8",
    )

    _systemctl(["daemon-reload"])
    cp = _systemctl(["enable", "--now", f"{UNIT_NAME}.timer"])
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "systemctl enable failed").strip())
    return _systemd_status()


def _systemd_clear() -> None:
    _systemctl(["disable", "--now", f"{UNIT_NAME}.timer"])
    for p in (_TIMER_PATH, _SERVICE_PATH):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    _systemctl(["daemon-reload"])


def _systemd_status() -> dict:
    active = _systemctl(["is-active", f"{UNIT_NAME}.timer"]).stdout.strip()
    if active != "active":
        return {"scheduled": False}
    info: dict = {"scheduled": True}
    # NextElapseUSecRealtime carries the human-readable next fire time in `show` output.
    cp = _systemctl(["show", f"{UNIT_NAME}.timer",
                     "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec"])
    for line in cp.stdout.splitlines():
        k, _, v = line.partition("=")
        v = v.strip()
        if k == "NextElapseUSecRealtime" and v:
            info["Next Run Time"] = v
        elif k == "LastTriggerUSec" and v:
            info["Last Run Time"] = v
    return info


def _validate_time(hhmm: str) -> None:
    try:
        h, m = hhmm.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError
    except Exception:
        raise RuntimeError(f"Time must be HH:MM (24-hour), got '{hhmm}'.")
