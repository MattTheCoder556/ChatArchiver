"""Drives a real browser via Patchright/Playwright using a persistent profile.

Login  (open_for_login): a VISIBLE window so the user can sign in once. Session is
saved in the profile dir — no passwords stored.

Export (run_export): runs HEADLESS in the background by default. Only if the background
session isn't authenticated (e.g. headless got challenged, or the login expired) does it
pop a visible window and retry. Export is incremental — see _run_export_in_context.

Anti-bot note: Cloudflare Turnstile detects the CDP automation channel itself, so we
prefer Patchright (a Playwright fork that closes those leaks), driving real Chrome.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

try:
    from patchright.sync_api import sync_playwright
    _USING_PATCHRIGHT = True
except ImportError:                       # pragma: no cover - fallback only
    from playwright.sync_api import sync_playwright
    _USING_PATCHRIGHT = False

from .markdown_writer import write_conversation
from .providers.base import Provider
from .sessions import load_config, profile_dir
from .store import content_key, load_manifest, save_manifest, updated_key

_STEALTH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
]
_IGNORE_DEFAULT_ARGS = ["--enable-automation"]
_HIDE_WEBDRIVER = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

class _NotAuthenticated(Exception):
    """Raised when an export context isn't logged in — triggers the headed retry."""


def _browser_attempts() -> list[dict]:
    """Ordered launch options to try: a user-set custom browser .exe first, then the
    auto-detected channels (real Chrome, then Edge), then the bundled Chromium."""
    attempts: list[dict] = []
    custom = (load_config().get("browser_path") or "").strip()
    if custom:
        attempts.append({"executable_path": custom})   # Brave/Vivaldi/Opera/portable…
    attempts.append({"channel": "chrome"})
    attempts.append({"channel": "msedge"})
    attempts.append({})                                 # bundled chromium
    return attempts


def _friendly_launch_error(e: Exception) -> Exception:
    msg = str(e)
    if "Executable" in msg or "install" in msg.lower():
        tool = "patchright" if _USING_PATCHRIGHT else "playwright"
        return RuntimeError(
            "No usable browser found. Install Google Chrome, or open a terminal here and run:\n"
            f"    python -m {tool} install chromium")
    return e


def _launch_context(pw, pdir, headless: bool):
    """Open a persistent context with the first browser option that works."""
    last_err: Exception | None = None
    for frag in _browser_attempts():
        try:
            kwargs: dict = dict(user_data_dir=str(pdir), headless=headless)
            if headless:
                kwargs["viewport"] = {"width": 1366, "height": 900}
            else:
                kwargs["no_viewport"] = True
            kwargs.update(frag)   # custom executable_path OR channel (or neither)
            if not _USING_PATCHRIGHT:
                kwargs["args"] = _STEALTH_ARGS
                kwargs["ignore_default_args"] = _IGNORE_DEFAULT_ARGS
            ctx = pw.chromium.launch_persistent_context(**kwargs)
            if not _USING_PATCHRIGHT:
                ctx.add_init_script(_HIDE_WEBDRIVER)
            return ctx
        except Exception as e:   # browser not found / failed → try the next option
            last_err = e
    raise _friendly_launch_error(last_err or RuntimeError("Could not launch a browser"))


def open_for_login(provider: Provider, status_cb: Callable[[str], None],
                   stop_event: threading.Event, timeout_s: int = 300) -> bool:
    """Open a visible browser for the user to log in. Returns True once authenticated."""
    pdir = profile_dir(provider.id)
    with sync_playwright() as pw:
        ctx = _launch_context(pw, pdir, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(provider.home_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            status_cb("Log in in the browser window. It closes itself once you're in.")
            deadline = time.time() + timeout_s
            while time.time() < deadline and not stop_event.is_set():
                try:
                    if provider.check_auth(page):
                        return True
                except Exception:
                    pass
                time.sleep(2)
            return False
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def run_export(provider: Provider, out_dir, status_cb: Callable[[str], None],
               progress_cb: Callable[[int, int, str], None],
               allow_headed_fallback: bool = True) -> dict:
    """Incremental export. Headless first; pop a window only if the session needs it.

    allow_headed_fallback=False (used by scheduled/headless runs) never opens a window —
    if the background session isn't authenticated it raises so the caller can log+skip.
    """
    try:
        return _export(provider, out_dir, True, status_cb, progress_cb)
    except _NotAuthenticated:
        if not allow_headed_fallback:
            raise RuntimeError("background session not logged in — open the app and Connect")
        status_cb(f"[{provider.label}] Background session needs attention — opening a window…")
        try:
            return _export(provider, out_dir, False, status_cb, progress_cb)
        except _NotAuthenticated:
            raise RuntimeError("Not connected. Click Connect and log in first.")


def _export(provider, out_dir, headless, status_cb, progress_cb) -> dict:
    pdir = profile_dir(provider.id)
    with sync_playwright() as pw:
        ctx = _launch_context(pw, pdir, headless=headless)
        try:
            return _run_export_in_context(provider, out_dir, ctx, status_cb, progress_cb)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _run_export_in_context(provider, out_dir, ctx, status_cb, progress_cb) -> dict:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(provider.home_url, wait_until="domcontentloaded", timeout=60000)
    if not provider.check_auth(page):
        raise _NotAuthenticated()

    status_cb(f"[{provider.label}] Reading your conversation list…")
    metas = provider.list_conversations(page)
    manifest = load_manifest(provider.id)
    total = len(metas)
    new = updated = unchanged = failed = 0

    for i, meta in enumerate(metas):
        if progress_cb:
            progress_cb(i + 1, total, meta.title)

        # Cheap skip: provider supplied a timestamp and it matches what we last wrote.
        cheap_key = updated_key(meta.updated_at)
        if meta.id and cheap_key and manifest.get(meta.id, {}).get("key") == cheap_key:
            unchanged += 1
            continue

        conv = provider.fetch_one(page, meta)
        if not conv:
            failed += 1
            continue

        key = cheap_key or content_key(conv)   # hash fallback for providers w/o timestamps
        prev = manifest.get(conv.id)
        if prev and prev.get("key") == key:
            unchanged += 1
            continue

        write_conversation(conv, out_dir)
        manifest[conv.id] = {"key": key, "title": conv.title}
        if prev:
            updated += 1
        else:
            new += 1

    save_manifest(provider.id, manifest)
    return {"new": new, "updated": updated, "unchanged": unchanged,
            "failed": failed, "total": total, "out_dir": str(out_dir / provider.id)}
