"""Provider-neutral data model for a single conversation and its messages."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def to_epoch(value) -> Optional[float]:
    """Best-effort convert a timestamp to unix seconds.

    Accepts a number, a numeric string, or an ISO-8601 string (the ChatGPT list and
    detail endpoints disagree on which they use). Returns None if it can't be parsed.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


@dataclass
class Message:
    role: str                       # "user" | "assistant" | "system" | "tool"
    text: str
    created_at: Optional[float] = None   # unix seconds, UTC


@dataclass
class Conversation:
    provider: str                   # provider id, e.g. "chatgpt"
    id: str
    title: str
    messages: list[Message] = field(default_factory=list)
    created_at: Optional[float] = None
    updated_at: Optional[float] = None

    def created_dt(self) -> Optional[datetime]:
        ts = to_epoch(self.created_at) or to_epoch(self.updated_at)
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)
