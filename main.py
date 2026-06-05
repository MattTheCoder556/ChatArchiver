"""Unified entry point for the packaged Chat Archiver executable.

  (no args)   -> open the desktop window (GUI)
  --export    -> run a headless incremental export (used by the scheduled task)
  --selftest  -> verify the bundled browser driver loads, write result to a file, exit

One exe serves both the window and the scheduled background export, dispatched by flag.
See ChatArchiver.spec for the PyInstaller build.
"""
from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]

    if "--selftest" in argv:
        from pathlib import Path
        try:
            from patchright.sync_api import sync_playwright
            with sync_playwright() as pw:
                ok = pw.chromium is not None
            msg = f"OK driver loaded (patchright); chromium={ok}"
        except Exception as e:  # bundling problem surfaces here
            msg = f"FAIL {e!r}"
        Path("selftest_result.txt").write_text(msg, encoding="utf-8")
        if sys.stdout:
            try:
                print(msg)
            except Exception:
                pass
        return 0 if msg.startswith("OK") else 1

    if "--export" in argv:
        from chatarchiver.cli import main as cli_main
        return cli_main()

    from chatarchiver.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
