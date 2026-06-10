"""Headless export entrypoint for scheduled (background timer / Task Scheduler) runs.

Exports every connected account incrementally, never opens a window, and writes a log to
~/.chatarchiver/logs/. This is what the systemd user timer (Linux) and the Windows
scheduled task invoke.

Most providers (ChatGPT, Claude, Grok, Gemini) export via cookie-handoff — they replay
the sites' APIs with the session from your everyday browser, so there's no saved profile
to check; we just attempt them and cookie_fetch skips any without a live session. Any
pure-Playwright provider (one that DID save a login profile) still goes through run_export.
"""
from __future__ import annotations

import sys
from datetime import datetime

from .cookie_fetch import COOKIE_PROVIDERS, WIP_PROVIDER_IDS
from .cookie_fetch import export as cookie_export
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
        did_anything = False

        # 1) Cookie-handoff providers (the working ones): attempt all, skip WIP. Those
        #    without a live browser session are reported and skipped by cookie_fetch.
        cookie_ids = tuple(p for p in COOKIE_PROVIDERS if p not in WIP_PROVIDER_IDS)
        if cookie_ids:
            did_anything = True
            try:
                res = cookie_export(providers=cookie_ids, out_dir=str(out), write=True, log=log)
                if any(isinstance(r, dict) and "error" in r for r in res.values()):
                    rc = 1
            except Exception as e:
                rc = 1
                log(f"[cookie-handoff] ERROR: {e}")

        # 2) Any Playwright-login providers that saved a profile (none today, but keep the
        #    path so a future profile-based provider exports on schedule too).
        for prov in PROVIDERS.values():
            if prov.id in COOKIE_PROVIDERS or not has_profile(prov.id):
                continue
            did_anything = True
            log(f"[{prov.label}] starting…")
            try:
                s = run_export(prov, out, log, lambda i, t, title: None,
                               allow_headed_fallback=False)
                log(f"[{prov.label}] {s['new']} new, {s['updated']} updated, "
                    f"{s['unchanged']} unchanged (of {s['total']})")
            except Exception as e:
                rc = 1
                log(f"[{prov.label}] ERROR: {e}")

        if not did_anything:
            log("No connected accounts. Nothing to do.")
        log("Done.")
    return rc


def main(argv=None) -> int:
    # The only command today is "export" (the default).
    return export_all()


if __name__ == "__main__":
    raise SystemExit(main())
