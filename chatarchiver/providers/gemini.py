"""Gemini (gemini.google.com) — experimental in-browser scraper.

Google exposes no conversation API, so unlike ChatGPT/Claude we can't fetch JSON.
Instead we drive the logged-in page: expand the sidebar history, open each
conversation, and read the rendered message turns out of the DOM.

Note on incremental export: the sidebar shows no per-chat timestamp, so we can't cheaply
tell what changed. list_conversations therefore returns metas with updated_at=None, and
the orchestrator falls back to hashing the fetched content — i.e. Gemini still re-reads
every chat, but only re-writes the ones whose content actually changed.

Gemini's markup uses obfuscated, frequently-changing class names, so EVERYTHING brittle
is here and written with fallback selectors.
"""
from __future__ import annotations

import re

from .base import ConvMeta, Provider
from ..models import Conversation, Message

_APP_URL = "https://gemini.google.com/app"

_LIST_SELECTORS = [
    '[data-test-id="conversation"]',
    '.conversation-items-container .conversation',
    'conversations-list .conversation',
    'side-navigation-content .conversation',
]

_EXTRACT_JS = r"""
() => {
  let u = document.querySelectorAll('user-query');
  let m = document.querySelectorAll('model-response');
  if (!u.length && !m.length) {
    u = document.querySelectorAll('.user-query-bubble-with-background, [data-test-id="user-query"]');
    m = document.querySelectorAll('message-content, .model-response-text, [data-test-id="model-response"]');
  }
  const all = [];
  u.forEach(e => all.push([e, 'user']));
  m.forEach(e => all.push([e, 'assistant']));
  all.sort((a, b) => {
    const pos = a[0].compareDocumentPosition(b[0]);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  return all.map(([e, role]) => ({role, text: (e.innerText || '').trim()})).filter(t => t.text);
}
"""

_SCROLL_TOP_JS = r"""
() => {
  const sc = document.querySelector('infinite-scroller, .chat-history, #chat-history, main');
  if (sc) sc.scrollTop = 0;
}
"""

# Logged-in detection. Does NOT navigate (the login poll runs it repeatedly). Returns
# true only on the Gemini app with a real signed-in element and no visible "Sign in".
_AUTH_JS = r"""
() => {
  if (!location.hostname.includes('gemini.google.com')) return false;
  const signedOut = Array.from(document.querySelectorAll('a, button')).some(el => {
    const t = (el.textContent || '').trim().toLowerCase();
    return t.startsWith('sign in') && el.offsetParent !== null;
  });
  if (signedOut) return false;
  const hasInput = !!document.querySelector(
    'rich-textarea, .ql-editor, [contenteditable="true"], input-area, [data-test-id="input-area"]');
  const hasSidebar = !!document.querySelector(
    'side-navigation-content, conversations-list, [data-test-id="conversation"]');
  return hasInput || hasSidebar;
}
"""


class GeminiProvider(Provider):
    id = "gemini"
    label = "Gemini (Google) — experimental"
    home_url = _APP_URL

    def check_auth(self, page) -> bool:
        import time
        deadline = time.time() + 6
        while True:
            try:
                if page.evaluate(_AUTH_JS):
                    return True
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            page.wait_for_timeout(700)

    def list_conversations(self, page) -> list[ConvMeta]:
        page.goto(self.home_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)            # let the Angular app boot
        loc = self._wait_for_list(page)        # poll (and reveal a collapsed sidebar)
        if loc is None:
            raise RuntimeError(
                "Gemini: couldn't find any conversations in the sidebar. The page layout "
                "likely changed — tell me and I'll adjust the selectors.")
        self._expand_history(page)
        loc = self._list_locator(page) or loc
        count = loc.count()
        metas: list[ConvMeta] = []
        for i in range(count):
            loc = self._list_locator(page) or loc
            try:
                title = (loc.nth(i).inner_text(timeout=5000) or "").strip().split("\n")[0]
            except Exception:
                title = ""
            metas.append(ConvMeta(title=title or f"Conversation {i + 1}", ref=i))
        return metas

    def fetch_one(self, page, meta: ConvMeta) -> Conversation | None:
        loc = self._list_locator(page)
        if not loc or loc.count() <= meta.ref:
            return None
        try:
            loc.nth(meta.ref).click(timeout=10000)
        except Exception:
            return None
        page.wait_for_timeout(2500)            # wait for the turns to render
        try:
            page.evaluate(_SCROLL_TOP_JS)
            page.wait_for_timeout(800)
        except Exception:
            pass
        turns = page.evaluate(_EXTRACT_JS) or []
        msgs = [Message(role=t["role"], text=t["text"]) for t in turns if t.get("text")]
        if not msgs:
            return None
        return Conversation(
            provider=self.id, id=self._conv_id(page, meta.ref), title=meta.title, messages=msgs)

    # ---- helpers ----
    def _list_locator(self, page):
        for sel in _LIST_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return None

    def _reveal_sidebar(self, page) -> None:
        """The history sidebar collapses to a hamburger; click it open if the list is hidden."""
        if self._list_locator(page):
            return
        for name in ("Main menu", "Expand menu", "Show side panel", "Menu", "Expand"):
            try:
                btn = page.get_by_role("button", name=re.compile(name, re.I))
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(1200)
                    if self._list_locator(page):
                        return
            except Exception:
                continue

    def _wait_for_list(self, page, timeout_s: int = 25):
        """Poll up to timeout_s for the conversation list to render (Angular is slow to
        hydrate, and the sidebar may need revealing). Returns a locator or None."""
        import time
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            loc = self._list_locator(page)
            if loc and loc.count() > 0:
                return loc
            self._reveal_sidebar(page)
            page.wait_for_timeout(1000)
        return None

    def _expand_history(self, page, max_clicks: int = 25) -> None:
        for _ in range(max_clicks):
            try:
                btn = page.get_by_role("button", name=re.compile("show more|more", re.I))
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    page.wait_for_timeout(1200)
                else:
                    break
            except Exception:
                break

    def _conv_id(self, page, fallback_index: int) -> str:
        m = re.search(r"/app/([A-Za-z0-9_-]+)", page.url or "")
        return m.group(1) if m else f"gemini-{fallback_index + 1}"
