"""Drives Mozilla Firefox via Playwright using a persistent profile.

We deliberately use **Firefox, not Chrome/Chromium**: it's outside Google's browser
lineage, so no Google binary runs and no Google telemetry is involved. Playwright ships
its own pinned Firefox build (installed via `python -m playwright install firefox`) — a
real Firefox, just version-matched to the driver.

Login  (open_for_login): a VISIBLE Firefox window so you sign in once. The session is
saved in the profile dir — no passwords stored.

Export (run_export): runs HEADLESS in the background using the saved session. Only if the
background session isn't authenticated (login expired, or a bot-check wants a real
window) does it pop a visible window and retry. Export is incremental — see
_run_export_in_context.

Captcha note: bot-checks (e.g. Cloudflare on ChatGPT) are tuned against headless
automation, not against a human signing in. Because the data fetches run from *inside* a
normally-loaded Firefox page (same cookies, same origin), the reliable path is: log in
once in the visible window, then let the headless export reuse that established session.
If a headless run gets challenged, check_auth returns False and we transparently retry in
a visible window.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .sessions import ensure_browsers_path, profile_dir

# Must run before Playwright resolves the browser path (critical for the frozen build).
ensure_browsers_path()

from playwright.sync_api import sync_playwright  # noqa: E402  (after ensure_browsers_path)

from .markdown_writer import write_conversation
from .providers.base import Provider
from .store import content_key, load_manifest, save_manifest, updated_key


class _NotAuthenticated(Exception):
    """Raised when an export context isn't logged in — triggers the headed retry."""


def _friendly_launch_error(e: Exception) -> Exception:
    msg = str(e)
    if "Executable" in msg or "install" in msg.lower():
        return RuntimeError(
            "Firefox isn't installed for Playwright. Open a terminal here and run:\n"
            "    python -m playwright install firefox")
    return e


def _launch_context(pw, pdir, headless: bool):
    """Open a persistent Firefox context — the saved login lives in `pdir`."""
    try:
        kwargs: dict = dict(user_data_dir=str(pdir), headless=headless)
        if headless:
            kwargs["viewport"] = {"width": 1366, "height": 900}
        else:
            kwargs["no_viewport"] = True
        return pw.firefox.launch_persistent_context(**kwargs)
    except Exception as e:
        raise _friendly_launch_error(e)


def open_for_login(provider: Provider, status_cb: Callable[[str], None],
                   stop_event: threading.Event, timeout_s: int = 300) -> bool:
    """Open a visible Firefox window for the user to log in. True once authenticated."""
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
    return _drive_export(provider, out_dir, page, status_cb, progress_cb)


def _drive_export(provider, out_dir, page, status_cb, progress_cb) -> dict:
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


def run_export_injected(provider, out_dir, cookies, user_agent, status_cb, progress_cb,
                        write: bool = True, sample: int = 3) -> dict:
    """Headless export using cookies carried in from the user's OWN browser — no login
    window. For sites that block an automated *login* but accept an already-authenticated
    session via cookies (Gemini: no JSON API, so we still render + scrape, but auth comes
    from your real Google session instead of a separate Playwright login).

    write=False = connectivity check (list only, no files).
    """
    with sync_playwright() as pw:
        browser = pw.firefox.launch(headless=True)
        try:
            # A wide/tall viewport keeps Gemini's history sidebar expanded (it collapses
            # to a hamburger when narrow, which hides the conversation list).
            ctx = browser.new_context(user_agent=user_agent,
                                      viewport={"width": 1500, "height": 1000})
            ctx.add_cookies(cookies)
            if write:
                try:
                    return _run_export_in_context(provider, out_dir, ctx, status_cb, progress_cb)
                except _NotAuthenticated:
                    raise RuntimeError("injected Google session not logged in (cookies expired?)")
            page = ctx.new_page()
            page.goto(provider.home_url, wait_until="domcontentloaded", timeout=60000)
            if not provider.check_auth(page):
                raise RuntimeError("injected Google session not logged in (cookies expired?)")
            metas = provider.list_conversations(page)
            return {"new": min(sample, len(metas)), "updated": 0, "unchanged": 0,
                    "failed": 0, "total": len(metas), "out_dir": str(out_dir / provider.id)}
        finally:
            try:
                browser.close()
            except Exception:
                pass
