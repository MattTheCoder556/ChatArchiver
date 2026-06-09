"""Self-update for run-from-source: pull the latest code from GitHub on launch.

Running from source means the app *is* the git checkout, so "updating" is just a
fast-forward `git pull`. run.py calls self_update() BEFORE importing any app code, so the
freshly pulled files are the ones Python loads (and re-execs to pick up run.py/this file).

Everything here is best-effort and must NEVER stop the app from launching: no git, no
network, not a clone, uncommitted local edits, or a non-fast-forward history all just
leave the code untouched and return (False, <reason>). Set CHATARCHIVER_NO_UPDATE=1 to
skip the check entirely.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# repo root = the folder holding .git (one level up from this package).
REPO_ROOT = Path(__file__).resolve().parents[1]

# Safety cap so a hung network can't freeze launch. A real offline failure (DNS) returns
# much faster than this; the cap only bites if a connection stalls mid-transfer.
_TIMEOUT = 20

# Don't flash a console window for git/pip when launched from the windowed exe.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(*args, timeout=_TIMEOUT):
    """Run a git command in the repo root. Returns CompletedProcess, or None on failure."""
    try:
        return subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except Exception:
        return None


def _ok(cp) -> bool:
    return cp is not None and cp.returncode == 0


def is_git_checkout() -> bool:
    """True only when we can actually self-update (git on PATH + a real clone)."""
    return bool(shutil.which("git")) and (REPO_ROOT / ".git").exists()


def _install_requirements(log) -> None:
    """A pulled commit may add a dependency; install so the new code can import."""
    log("[update] requirements.txt changed — installing dependencies…")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                            str(REPO_ROOT / "requirements.txt")],
                           cwd=str(REPO_ROOT), capture_output=True, text=True,
                           timeout=600, creationflags=_NO_WINDOW)
        log("[update] dependencies installed." if r.returncode == 0
            else "[update] pip install reported errors — see console; the app may still run.")
    except Exception as e:
        log(f"[update] could not install dependencies ({e}); the app may still run.")


def self_update(log=print) -> tuple[bool, str]:
    """Fast-forward the source checkout to the latest commit on its upstream branch.

    Returns (updated, message). `updated` is True only when new commits were applied.
    `message` is a short human line for the log (empty when there's nothing to say).
    """
    if not is_git_checkout():
        return False, ""

    # Never clobber uncommitted work — this checkout is also where you edit the code.
    status = _git("status", "--porcelain")
    if status is None:
        return False, ""                       # git unavailable mid-call; stay quiet
    if status.stdout.strip():
        return False, "local changes present — auto-update skipped"

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not _ok(branch) or not branch.stdout.strip():
        return False, ""
    br = branch.stdout.strip()

    # Resolve the branch's upstream remote (fallback: origin) for an explicit pull.
    up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{br}@{{u}}")
    remote = (up.stdout.strip().split("/", 1)[0]
              if _ok(up) and "/" in up.stdout else "origin")

    before = _git("rev-parse", "HEAD")
    if not _ok(before):
        return False, ""

    pull = _git("pull", "--ff-only", remote, br)
    if not _ok(pull):
        last = ""
        if pull and pull.stderr.strip():
            last = pull.stderr.strip().splitlines()[-1]
        return False, f"auto-update skipped: {last or 'fetch failed (offline?)'}"

    after = _git("rev-parse", "HEAD")
    if not _ok(after) or before.stdout.strip() == after.stdout.strip():
        return False, ""                       # already up to date

    old, new = before.stdout.strip(), after.stdout.strip()
    changed = _git("diff", "--name-only", old, new)
    if _ok(changed) and "requirements.txt" in changed.stdout.split():
        _install_requirements(log)
    return True, f"updated {old[:7]} → {new[:7]}"
