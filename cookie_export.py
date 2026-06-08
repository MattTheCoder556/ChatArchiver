"""Cookie-handoff export — archive ChatGPT/Claude using your everyday browser's session.

No automated browser, no Google binary, no Cloudflare fight: you stay logged in normally,
this reads that session and replays the sites' APIs with a browser-impersonating HTTP
client. See chatarchiver/cookie_fetch.py.

Examples:
    python cookie_export.py --check                 # verify it can reach your accounts
    python cookie_export.py                          # export ChatGPT + Claude
    python cookie_export.py --providers claude       # just Claude
    python cookie_export.py --browser firefox --out "~/Chat Archive"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from chatarchiver.cookie_fetch import export


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive ChatGPT/Claude via your browser's cookies.")
    ap.add_argument("--providers", default="chatgpt,claude",
                    help="comma list: chatgpt,claude (default both)")
    ap.add_argument("--browser", default="auto",
                    help="auto (default) | firefox | chrome | vivaldi | brave | chromium | edge")
    ap.add_argument("--out", default=None, help="output folder (default: your configured one)")
    ap.add_argument("--check", action="store_true",
                    help="connectivity check only — no files written")
    args = ap.parse_args()

    providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())
    out = str(Path(args.out).expanduser()) if args.out else None
    results = export(providers=providers, browser=args.browser, out_dir=out,
                     write=not args.check)
    ok = any(isinstance(r, dict) and "error" not in r for r in results.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
