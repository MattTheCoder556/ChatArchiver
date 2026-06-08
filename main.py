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
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                ok = pw.firefox is not None
            msg = f"OK driver loaded (playwright firefox); firefox={ok}"
        except Exception as e:  # bundling problem surfaces here
            msg = f"FAIL {e!r}"
        Path("selftest_result.txt").write_text(msg, encoding="utf-8")
        if sys.stdout:
            try:
                print(msg)
            except Exception:
                pass
        return 0 if msg.startswith("OK") else 1

    if "--cookie-check" in argv or "--cookie-export" in argv:
        from chatarchiver.cookie_fetch import export
        providers = ("chatgpt", "claude")
        if "--providers" in argv:
            val = argv[argv.index("--providers") + 1] if argv.index("--providers") + 1 < len(argv) else ""
            providers = tuple(p.strip() for p in val.split(",") if p.strip()) or providers
        res = export(providers=providers, write="--cookie-export" in argv)
        return 0 if any(isinstance(r, dict) and "error" not in r
                        for r in res.values()) else 1

    if "--export" in argv:
        from chatarchiver.cli import main as cli_main
        return cli_main()

    from chatarchiver.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
