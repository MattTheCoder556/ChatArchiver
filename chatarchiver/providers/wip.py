"""Work-in-progress providers — wired into the UI but not yet verified end-to-end.

Each shows as a row, its "Log in" opens the site, and Export detects whether you have a
session — but the per-service list/fetch endpoints aren't implemented yet (several of
these use non-cookie auth or undocumented APIs that need verifying against a live login).

To promote one to a real provider: implement its list + per-conversation endpoints in
chatarchiver/cookie_fetch.py (HTTP replay, like chatgpt/claude) and drop it from the WIP
set there. The label's "(WIP)" suffix then comes off here.
"""
from __future__ import annotations

from .base import Provider


class _WIPProvider(Provider):
    """Placeholder: satisfies the registry/UI; the cookie path handles it as WIP."""
    def check_auth(self, page) -> bool:
        return False

    def list_conversations(self, page):
        return []

    def fetch_one(self, page, meta):
        return None


def _make(pid: str, label: str, home: str) -> _WIPProvider:
    cls = type(f"{pid.capitalize()}Provider", (_WIPProvider,),
               {"id": pid, "label": label, "home_url": home})
    return cls()


# Order here = order shown under the working providers in the window.
WIP_PROVIDERS = [
    _make("deepseek",   "DeepSeek (WIP)",          "https://chat.deepseek.com/"),
    _make("mistral",    "Mistral · Le Chat (WIP)", "https://chat.mistral.ai/"),
    _make("perplexity", "Perplexity (WIP)",        "https://www.perplexity.ai/"),
    _make("poe",        "Poe (WIP)",               "https://poe.com/"),
    _make("grok",       "Grok (WIP)",              "https://grok.com/"),
    _make("copilot",    "Copilot (WIP)",           "https://copilot.microsoft.com/"),
]
