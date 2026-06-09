"""Cookie-handoff export — no automated browser, no Google binary, no Cloudflare fight.

You stay logged into ChatGPT / Claude in your normal everyday browser. This reads that
browser's session cookies and replays each site's own private API with curl_cffi, which
speaks a real browser's TLS fingerprint — so Cloudflare sees an ordinary request and the
`cf_clearance` cookie you already earned still applies. Nothing is launched or automated.

Covered: **ChatGPT and Claude** (they expose JSON APIs). NOT Gemini — it has no API (the
browser path scraped its DOM), so there's nothing to replay over HTTP; use Google Takeout
for Gemini history.

Reuses the existing per-provider JSON parsers (providers/chatgpt.py, providers/claude.py),
the data model, the incremental manifest (store.py) and the markdown writer — only the
*transport* changes from "inside a Playwright page" to "curl_cffi with your cookies".
"""
from __future__ import annotations

import glob
import os
import base64
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import browser_cookie3 as bc3
from curl_cffi import requests as creq

from .markdown_writer import write_conversation
from .models import Conversation, Message
from .providers import chatgpt as _cg
from .providers import claude as _cl
from .sessions import output_dir_from_config
from .store import content_key, load_manifest, save_manifest, updated_key

# The cookie that proves you're actually logged in (vs. just having visited the site).
_SESSION_COOKIE = {
    "chatgpt.com": ("__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0"),
    "claude.ai": ("sessionKey",),
    "grok.com": ("sso",),
}
_DOMAIN = {
    "chatgpt": "chatgpt.com",
    "claude": "claude.ai",
    "gemini": "gemini.google.com",
    "grok": "grok.com",
    # WIP — wired into the UI; list/fetch endpoints not implemented/verified yet.
    "deepseek": "chat.deepseek.com",
}

# Not yet implemented end-to-end: shown in the app, detect login, but export is stubbed.
WIP_PROVIDER_IDS = ("deepseek",)

# Providers the GUI routes through cookie-handoff instead of a Playwright login window.
# ChatGPT/Claude: pure HTTP (they have JSON APIs). Gemini: no API, so we inject your real
# Google cookies into a headless browser and scrape the DOM. The WIP ones are routed here
# too (for login + session detection) until their endpoints are implemented.
COOKIE_PROVIDERS = tuple(_DOMAIN)


def site_url(provider_id: str) -> str:
    """Home URL to open in the user's own browser so they can log in."""
    return f"https://{_DOMAIN[provider_id]}/"

# curl_cffi's available Firefox TLS profiles (newest first). We pick the nearest to the
# installed Firefox so the JA3 fingerprint matches what earned the cf_clearance cookie.
_FF_TARGETS = [147, 144, 135, 133]


# ---------------------------------------------------------------- browser / cookies ----

def _firefox_cookie_files() -> list[str]:
    """Find Firefox cookies.sqlite across Linux (snap/native/flatpak), macOS and Windows."""
    pats = [
        # Linux
        "~/snap/firefox/common/.mozilla/firefox/*.default*/cookies.sqlite",
        "~/.mozilla/firefox/*.default*/cookies.sqlite",
        "~/.var/app/org.mozilla.firefox/.mozilla/firefox/*.default*/cookies.sqlite",
        # macOS
        "~/Library/Application Support/Firefox/Profiles/*.default*/cookies.sqlite",
    ]
    out: list[str] = []
    for p in pats:
        out += sorted(glob.glob(os.path.expanduser(p)))
    # Windows: %APPDATA%\Mozilla\Firefox\Profiles\<profile>\cookies.sqlite
    appdata = os.environ.get("APPDATA")
    if appdata:
        out += sorted(glob.glob(os.path.join(appdata, "Mozilla", "Firefox", "Profiles",
                                             "*.default*", "cookies.sqlite")))
    return out


def _firefox_version() -> int:
    try:
        out = subprocess.run(["firefox", "--version"], capture_output=True,
                             text=True, timeout=15).stdout
        m = re.search(r"(\d+)\.\d", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return _FF_TARGETS[0]


def _firefox_ua(major: int) -> str:
    plat = "X11; Linux x86_64"
    if sys.platform == "darwin":
        plat = "Macintosh; Intel Mac OS X 10.15"
    elif sys.platform.startswith("win"):
        plat = "Windows NT 10.0; Win64; x64"
    return f"Mozilla/5.0 ({plat}; rv:{major}.0) Gecko/20100101 Firefox/{major}.0"


def _impersonate(major: int) -> str:
    return f"firefox{min(_FF_TARGETS, key=lambda t: abs(t - major))}"


def _fresh_firefox_copy(src: str) -> str:
    """Copy cookies.sqlite + its WAL to a temp dir and checkpoint it, so reads see cookies
    Firefox wrote moments ago (e.g. a token it just refreshed) instead of the stale main
    DB. This is the 'flush'. Returns the path to the checkpointed copy."""
    d = tempfile.mkdtemp(prefix="cha_ff_")
    dst = os.path.join(d, "cookies.sqlite")
    shutil.copy2(src, dst)
    for ext in ("-wal", "-shm"):
        if os.path.exists(src + ext):
            shutil.copy2(src + ext, dst + ext)
    try:
        con = sqlite3.connect(dst)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
        con.close()
    except Exception:
        pass
    return dst


def _load_jar(domain: str, browser: str, ff_file: str | None):
    if browser == "firefox":
        if ff_file and os.path.exists(ff_file + "-wal"):   # WAL present → flush it first
            tmp = _fresh_firefox_copy(ff_file)
            try:
                return bc3.firefox(cookie_file=tmp, domain_name=domain)
            finally:
                shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
        return bc3.firefox(cookie_file=ff_file, domain_name=domain)
    return getattr(bc3, browser)(domain_name=domain)


def _has_session(jar, domain: str) -> bool:
    names = {c.name for c in jar}
    return any(s in names for s in _SESSION_COOKIE[domain])


def _pick_browser(domain: str, browser: str, ff_file: str | None):
    """Return (browser_name, cookie_jar) for the first browser holding a live session."""
    if browser != "auto":
        jar = _load_jar(domain, browser, ff_file)
        return (browser, jar) if _has_session(jar, domain) else (browser, None)

    # Always try Firefox: if ff_file is None, browser_cookie3 auto-detects per-OS.
    candidates: list[tuple[str, object]] = [("firefox", ff_file)]
    for name in ("chrome", "vivaldi", "brave", "chromium", "edge"):
        if hasattr(bc3, name):
            candidates.append((name, None))
    for name, ff in candidates:
        try:
            jar = _load_jar(domain, name, ff)
            if _has_session(jar, domain):
                return name, jar
        except Exception:
            continue
    return None, None


def _cookie_dict(jar) -> dict:
    return {c.name: c.value for c in jar}


def _google_cookies_pw(browser: str, ff_file: str | None) -> list[dict]:
    """Google login cookies (for Gemini) in Playwright add_cookies() format."""
    if browser != "auto":
        cands = [browser]
    else:
        # Always include Firefox (ff_file None → browser_cookie3 auto-detects per-OS).
        cands = ["firefox"]
        cands += [n for n in ("chrome", "vivaldi", "brave", "chromium", "edge") if hasattr(bc3, n)]
    for name in cands:
        try:
            jar = _load_jar("google.com", name, ff_file)
            have = {c.name for c in jar}
            if "__Secure-1PSID" in have or "SID" in have:
                return [{"name": c.name, "value": c.value, "domain": c.domain,
                         "path": c.path or "/", "secure": bool(getattr(c, "secure", False))}
                        for c in jar]
        except Exception:
            continue
    return []


# --------------------------------------------------------------------- fetch + write ----

def _process(pid, items, get_id, get_title, get_created, get_updated, fetch_raw, parse,
             out: Path, log, write: bool, sample: int, progress=None) -> dict:
    """Shared loop: incremental-skip, fetch, parse, (optionally) write markdown."""
    manifest = load_manifest(pid)
    total = len(items)
    new = updated = unchanged = failed = 0

    for i, it in enumerate(items):
        cid, title = get_id(it), get_title(it)
        if progress:
            progress(i + 1, total, title)
        cu = get_updated(it)
        cheap = updated_key(cu)
        if write and cheap and manifest.get(cid, {}).get("key") == cheap:
            unchanged += 1
            continue
        if not write and i >= sample:           # check-mode: only sample the first few
            break
        try:
            msgs = parse(fetch_raw(it))
        except Exception:
            failed += 1
            continue
        if not msgs:
            failed += 1
            continue
        conv = Conversation(provider=pid, id=cid, title=title,
                            created_at=get_created(it), updated_at=cu, messages=msgs)
        if not write:
            new += 1                            # "would write" in check-mode
            continue
        key = cheap or content_key(conv)
        prev = manifest.get(cid)
        if prev and prev.get("key") == key:
            unchanged += 1
            continue
        write_conversation(conv, out)
        manifest[cid] = {"key": key, "title": title}
        updated += 1 if prev else 0
        new += 0 if prev else 1
        if i % 10 == 0:
            log(f"    {pid}: {i + 1}/{total}…")

    if write:
        save_manifest(pid, manifest)
    return {"new": new, "updated": updated, "unchanged": unchanged,
            "failed": failed, "total": total}


def _export_chatgpt(s, cookies, out, log, write, sample, progress=None) -> dict:
    base = "https://chatgpt.com"
    r = s.get(f"{base}/api/auth/session", cookies=cookies, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"/api/auth/session HTTP {r.status_code} "
                           f"(Cloudflare block or session expired)")
    token = (r.json() or {}).get("accessToken")
    if not token:
        raise RuntimeError("no accessToken — not logged into ChatGPT in this browser")
    headers = {"Authorization": f"Bearer {token}"}

    items, offset, limit = [], 0, 100
    while offset < 20000:
        r = s.get(f"{base}/backend-api/conversations?offset={offset}&limit={limit}&order=updated",
                  headers=headers, cookies=cookies, timeout=30)
        if r.status_code == 401:
            raise RuntimeError("ChatGPT access token expired — open chatgpt.com in the "
                               "browser the tool reads (Firefox) to refresh it, then retry.")
        if r.status_code != 200:
            raise RuntimeError(f"conversations list HTTP {r.status_code}")
        batch = (r.json() or {}).get("items", [])
        items += batch
        if len(batch) < limit:
            break
        offset += limit

    def fetch_raw(it):
        return s.get(f"{base}/backend-api/conversation/{it['id']}",
                     headers=headers, cookies=cookies, timeout=60).json()

    return _process("chatgpt", items,
                    get_id=lambda it: it["id"],
                    get_title=lambda it: it.get("title") or "Untitled",
                    get_created=lambda it: it.get("create_time"),
                    get_updated=lambda it: it.get("update_time"),
                    fetch_raw=fetch_raw, parse=_cg._parse,
                    out=out, log=log, write=write, sample=sample, progress=progress)


def _export_claude(s, cookies, out, log, write, sample, progress=None) -> dict:
    base = "https://claude.ai"
    r = s.get(f"{base}/api/organizations", cookies=cookies, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"/api/organizations HTTP {r.status_code} "
                           f"(Cloudflare block or session expired)")
    orgs = r.json() or []
    if not orgs:
        raise RuntimeError("no organizations — not logged into Claude in this browser")
    org = orgs[0]["uuid"]

    r = s.get(f"{base}/api/organizations/{org}/chat_conversations", cookies=cookies, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"chat_conversations list HTTP {r.status_code}")
    items = r.json() or []

    def fetch_raw(it):
        return s.get(f"{base}/api/organizations/{org}/chat_conversations/{it['uuid']}"
                     f"?tree=True&rendering_mode=raw", cookies=cookies, timeout=60).json()

    return _process("claude", items,
                    get_id=lambda it: it["uuid"],
                    get_title=lambda it: it.get("name") or "Untitled",
                    get_created=lambda it: _cl._parse_ts(it.get("created_at")),
                    get_updated=lambda it: _cl._parse_ts(it.get("updated_at")),
                    fetch_raw=fetch_raw, parse=_cl._parse,
                    out=out, log=log, write=write, sample=sample, progress=progress)


_GROK_RENDER = re.compile(r"<grok:render\b.*?</grok:render>", re.DOTALL)


def _parse_grok(raw) -> list:
    out = []
    for resp in (raw or {}).get("responses", []):
        role = "user" if (resp.get("sender") or "").lower() == "human" else "assistant"
        text = _GROK_RENDER.sub("", resp.get("message") or "")   # drop inline citation tags
        if text.strip():
            out.append(Message(role=role, text=text, created_at=_cl._parse_ts(resp.get("createTime"))))
    return out


def _export_grok(s, cookies, out, log, write, sample, progress=None) -> dict:
    """Grok (grok.com, xAI): list conversations, then fetch each one's responses."""
    base = "https://grok.com"
    page_size = 1000
    r = s.get(f"{base}/rest/app-chat/conversations?pageSize={page_size}", cookies=cookies, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"conversations list HTTP {r.status_code}")
    items = (r.json() or {}).get("conversations", [])
    if len(items) >= page_size:
        log(f"[grok] note: hit the {page_size}-conversation page cap — older ones may be missed.")

    def fetch_raw(it):
        cid = it["conversationId"]
        return s.get(f"{base}/rest/app-chat/conversations/{cid}/responses",
                     cookies=cookies, timeout=60).json()

    return _process("grok", items,
                    get_id=lambda it: it["conversationId"],
                    get_title=lambda it: it.get("title") or "Untitled",
                    get_created=lambda it: _cl._parse_ts(it.get("createTime")),
                    get_updated=lambda it: _cl._parse_ts(it.get("modifyTime")),
                    fetch_raw=fetch_raw, parse=_parse_grok,
                    out=out, log=log, write=write, sample=sample, progress=progress)


_EXPORTERS = {"chatgpt": _export_chatgpt, "claude": _export_claude, "grok": _export_grok}


def _export_gemini(out: Path, log, write: bool, browser: str, ff_file, ua, sample: int,
                   progress=None) -> dict:
    """Gemini has no JSON API: inject the user's real Google cookies into a headless
    browser and reuse the DOM scraper. No separate login window."""
    cookies = _google_cookies_pw(browser, ff_file)
    if not cookies:
        log("[gemini] no Google session found in your browser(s). "
            "Open gemini.google.com, log in, then retry.")
        return {"error": "no Google session"}
    log(f"[gemini] {'EXPORT' if write else 'CHECK'} via injected Google cookies "
        f"({len(cookies)} cookies), headless Firefox")
    try:
        from .playwright_runner import run_export_injected
        from .providers.gemini import GeminiProvider
        r = run_export_injected(GeminiProvider(), out, cookies, ua,
                                lambda m: log(m), progress or (lambda i, t, ti: None),
                                write=write, sample=sample)
    except Exception as e:
        log(f"[gemini] ERROR: {e}")
        return {"error": str(e)}
    verb = "would write" if not write else "wrote"
    log(f"[gemini] OK — {r['total']} conversations found; {verb} {r['new']} new"
        + (f", {r['updated']} updated, {r['unchanged']} unchanged" if write else "")
        + (f"  -> {out / 'gemini'}" if write else ""))
    return r


def _export_wip(pid: str, ff_file, log) -> dict:
    """WIP placeholder: detect a login (cookie-handoff foundation), then report that the
    export endpoints for this service aren't implemented yet — never fakes success."""
    domain = _DOMAIN[pid]
    n = 0
    for name in ("firefox", "chrome", "vivaldi", "brave", "chromium", "edge"):
        if name != "firefox" and not hasattr(bc3, name):
            continue
        try:
            jar = _load_jar(domain, name, ff_file)
            n = len(list(jar))
            if n:
                break
        except Exception:
            continue
    state = (f"{n} cookies found for {domain} — looks logged in"
             if n else f"no session for {domain} — log in there first")
    log(f"[{pid}] WIP — wired into the app, but its export endpoints aren't implemented "
        f"yet. {state}. Ask me to finish {pid} and I'll implement + test it live.")
    return {"wip": True, "cookies": n}


def export(providers=("chatgpt", "claude"), browser="auto", out_dir=None,
           log=print, write=True, sample=2, progress=None) -> dict:
    """Export the given providers via cookie-handoff. write=False = check-only (no files).

    progress(done, total, title) is called per conversation (for a GUI progress bar)."""
    out = Path(out_dir) if out_dir else output_dir_from_config()
    ff_files = _firefox_cookie_files()
    ff_file = ff_files[0] if ff_files else None
    major = _firefox_version()
    ua, imp = _firefox_ua(major), _impersonate(major)

    results = {}
    for pid in providers:
        if pid in WIP_PROVIDER_IDS:
            results[pid] = _export_wip(pid, ff_file, log)
            continue
        if pid == "gemini":
            results[pid] = _export_gemini(out, log, write, browser, ff_file, ua, sample, progress)
            continue
        domain = _DOMAIN.get(pid)
        if not domain or pid not in _EXPORTERS:
            log(f"[{pid}] not supported by cookie-handoff. Skipping.")
            continue
        br, jar = _pick_browser(domain, browser, ff_file)
        if not jar:
            log(f"[{pid}] no logged-in {domain} session found in your browser(s). "
                f"Open {domain}, log in, then retry.")
            continue
        cookies = _cookie_dict(jar)
        s = creq.Session(impersonate=imp)
        s.headers["User-Agent"] = ua
        mode = "EXPORT" if write else "CHECK"
        log(f"[{pid}] {mode} via {br} ({len(cookies)} cookies), impersonate={imp}, FF/{major}")
        try:
            r = _EXPORTERS[pid](s, cookies, out, log, write, sample, progress)
            verb = "would write" if not write else "wrote"
            log(f"[{pid}] OK — {r['total']} conversations found; {verb} {r['new']} new"
                + (f", {r['updated']} updated, {r['unchanged']} unchanged" if write else "")
                + (f", {r['failed']} failed" if r['failed'] else "")
                + (f"  -> {out / pid}" if write else ""))
            results[pid] = r
        except Exception as e:
            log(f"[{pid}] ERROR: {e}")
            results[pid] = {"error": str(e)}
        finally:
            s.close()
    return results


# ------------------------------------------------------------------ session status ----

def _jwt_expired(token: str):
    """True if the JWT's exp is in the past, False if valid, None if undecodable."""
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p)).get("exp")
        return bool(exp and exp < time.time())
    except Exception:
        return None


def _status_one(pid, browser, ff_file, ua):
    """Return (state, short, detail). state in: ok | stale | out | error."""
    if pid == "gemini":
        if _google_cookies_pw(browser, ff_file):
            return ("ok", "ready ✓", "Google session found")
        return ("out", "log in", "no Google session — log into gemini.google.com in Firefox")

    domain = _DOMAIN[pid]
    _, jar = _pick_browser(domain, browser, ff_file)
    if not jar:
        return ("out", "log in", f"not logged into {domain} in your browser")
    cookies = _cookie_dict(jar)
    s = creq.Session(impersonate="firefox147")
    s.headers["User-Agent"] = ua
    try:
        if pid == "chatgpt":
            r = s.get("https://chatgpt.com/api/auth/session", cookies=cookies, timeout=20)
            tok = (r.json() or {}).get("accessToken") if r.ok else None
            if not tok:
                return ("out", "log in", "ChatGPT session not active — log in at chatgpt.com in Firefox")
            if _jwt_expired(tok):
                return ("stale", "token stale",
                        "ChatGPT access token expired — open chatgpt.com in Firefox to refresh, "
                        "then Refresh again")
            return ("ok", "ready ✓", "ChatGPT ready")
        if pid == "claude":
            r = s.get("https://claude.ai/api/organizations", cookies=cookies, timeout=20)
            ok = r.ok and isinstance(r.json(), list) and len(r.json()) > 0
            return ("ok", "ready ✓", "Claude ready") if ok else ("out", "log in", "Claude session not active")
        if pid == "grok":
            r = s.get("https://grok.com/api/auth/session", cookies=cookies, timeout=20)
            ok = r.ok and (r.json() or {}).get("status") == "authenticated"
            return ("ok", "ready ✓", "Grok ready") if ok else ("out", "log in", "Grok session not active")
    finally:
        s.close()
    return ("out", "—", "")


def session_status(providers=("chatgpt", "claude", "gemini", "grok"), browser="auto") -> dict:
    """Re-read cookies (WAL-flushed) and report each provider's live login/freshness.
    Powers the UI 'Refresh sessions' button — no export, just a quick health check."""
    ff_files = _firefox_cookie_files()
    ff_file = ff_files[0] if ff_files else None
    ua = _firefox_ua(_firefox_version())
    out = {}
    for pid in providers:
        try:
            out[pid] = _status_one(pid, browser, ff_file, ua)
        except Exception as e:
            out[pid] = ("error", "error", str(e)[:80])
    return out
