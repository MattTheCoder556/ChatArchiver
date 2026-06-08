"""Registry of available providers. Add a new service by importing it here."""
from __future__ import annotations

from .chatgpt import ChatGPTProvider
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .grok import GrokProvider
from .wip import WIP_PROVIDERS

# Order here = order shown in the window: the working providers, then the WIP ones.
PROVIDERS = {p.id: p for p in (ChatGPTProvider(), ClaudeProvider(), GeminiProvider(),
                               GrokProvider(), *WIP_PROVIDERS)}
