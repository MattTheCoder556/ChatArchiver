"""Registry of available providers. Add a new service by importing it here."""
from __future__ import annotations

from .chatgpt import ChatGPTProvider
from .claude import ClaudeProvider
from .gemini import GeminiProvider

# Order here = order shown in the window.
PROVIDERS = {p.id: p for p in (ChatGPTProvider(), ClaudeProvider(), GeminiProvider())}
