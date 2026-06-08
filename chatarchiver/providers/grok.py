"""Grok (grok.com, xAI).

Exported via cookie-handoff (chatarchiver/cookie_fetch.py: _export_grok) — it replays
grok.com's own REST API (`/rest/app-chat/conversations` + `/{id}/responses`) with your
browser session. The Playwright methods below are unused; this class only supplies the
UI row and the login URL.
"""
from __future__ import annotations

from .base import Provider


class GrokProvider(Provider):
    id = "grok"
    label = "Grok (xAI)"
    home_url = "https://grok.com/"

    def check_auth(self, page) -> bool:
        return False

    def list_conversations(self, page):
        return []

    def fetch_one(self, page, meta):
        return None
