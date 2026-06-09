"""Launch the Chat Archiver window (run-from-source).

Before importing any app code we pull the latest source from GitHub, so the freshly
updated files are the ones Python loads. If an update was applied we re-exec the
interpreter once, so even run.py and the updater itself run as their new versions.

Set CHATARCHIVER_NO_UPDATE=1 to skip the check (e.g. while developing offline).
"""
from __future__ import annotations

import os
import sys


def _auto_update() -> None:
    if os.environ.get("CHATARCHIVER_NO_UPDATE") == "1":
        return
    if os.environ.get("CHATARCHIVER_UPDATED") == "1":
        return                                   # already re-exec'd this launch; never loop
    try:
        from chatarchiver.updater import self_update
        updated, msg = self_update()
    except Exception as e:
        updated, msg = False, f"update check error: {e}"
    if msg:
        os.environ["CHATARCHIVER_UPDATE_MSG"] = msg   # the window logs this on startup
        print(f"[update] {msg}")
    if updated:
        os.environ["CHATARCHIVER_UPDATED"] = "1"
        os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
    _auto_update()
    from chatarchiver.app import main
    main()
