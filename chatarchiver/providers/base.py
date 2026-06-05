"""Provider interface. Each chat service implements one subclass.

Split into two steps so incremental export can skip unchanged chats *before* paying for
the expensive per-conversation fetch:

  - list_conversations(page) -> [ConvMeta]   cheap: ids + titles + updated_at
  - fetch_one(page, meta)    -> Conversation  expensive: full message content

For ChatGPT/Claude the list carries a real `updated_at`, so unchanged chats are skipped
without fetching. Gemini's sidebar exposes no timestamp, so its metas have
updated_at=None and the orchestrator falls back to a content hash after fetching.

Adding Copilot / DeepSeek / Perplexity / Grok later is still one new file.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..models import Conversation

ProgressCb = Optional[Callable[[int, int, str], None]]


@dataclass
class ConvMeta:
    """Lightweight conversation descriptor from the cheap list step."""
    title: str
    id: Optional[str] = None          # may be unknown until fetch (Gemini)
    created_at: Any = None
    updated_at: Any = None            # None when the provider exposes no timestamp
    ref: Any = None                   # provider-private handle (e.g. Gemini sidebar index)


class Provider(ABC):
    id: str = ""
    label: str = ""
    home_url: str = ""

    @abstractmethod
    def check_auth(self, page) -> bool:
        """Return True if the session in `page` is logged in."""

    @abstractmethod
    def list_conversations(self, page) -> list[ConvMeta]:
        """Cheap listing of all conversations (no message bodies)."""

    @abstractmethod
    def fetch_one(self, page, meta: ConvMeta) -> Optional[Conversation]:
        """Download the full conversation for one ConvMeta. None to skip."""
