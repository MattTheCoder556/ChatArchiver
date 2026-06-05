"""Headless export entrypoint for scheduled (Task Scheduler) runs — no GUI.

Exports every connected account incrementally, never opens a window, and writes a log to
~/.chatarchiver/logs/. This is what the Windows scheduled task invokes.
"""
from __future__ import annotations

import sys
from datetime import datetime

from .playwright_runner import run_export
from .providers import PROVIDERS
from .sessions import LOGS_DIR, has_profile, output_dir_from_config


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def export_all() -> int:
    out = output_dir_from_config()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"export-{datetime.now():%Y%m%d-%H%M%S}.log"
    rc = 0

    with open(log_path, "w", encoding="utf-8") as fh:
        def log(msg: str) -> None:
            line = f"{_stamp()}  {msg}"
            if sys.stdout:                      # None in a windowed (no-console) exe
                try:
                    print(line)
                except Exception:
                    pass
            fh.write(line + "\n")
            fh.flush()

        log(f"Chat Archiver scheduled export -> {out}")
        providers = [p for p in PROVIDERS.values() if has_profile(p.id)]
        if not providers:
            log("No connected accounts (no saved logins). Nothing to do.")
            return 0

        for prov in providers:
            log(f"[{prov.label}] starting…")
            try:
                s = run_export(prov, out, log, lambda i, t, title: None,
                               allow_headed_fallback=False)
                log(f"[{prov.label}] {s['new']} new, {s['updated']} updated, "
                    f"{s['unchanged']} unchanged (of {s['total']})")
            except Exception as e:
                rc = 1
                log(f"[{prov.label}] ERROR: {e}")
        log("Done.")
    return rc


def main(argv=None) -> int:
    # The only command today is "export" (the default).
    return export_all()


if __name__ == "__main__":
    raise SystemExit(main())
