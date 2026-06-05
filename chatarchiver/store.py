"""Per-provider export manifest — what we've already written and its change-marker.

Lets export skip unchanged conversations. The marker is either the conversation's
`updated_at` (ChatGPT/Claude) or a hash of its content (Gemini, which has no timestamp).

Manifest shape: { conversation_id: {"key": "<marker>", "title": "..."} }
stored at ~/.chatarchiver/manifests/<provider>.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import Conversation, to_epoch
from .sessions import APP_DIR

MANIFEST_DIR = APP_DIR / "manifests"


def _path(provider_id: str) -> Path:
    return MANIFEST_DIR / f"{provider_id}.json"


def load_manifest(provider_id: str) -> dict:
    p = _path(provider_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(provider_id: str, data: dict) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    _path(provider_id).write_text(json.dumps(data, indent=2), encoding="utf-8")


def updated_key(updated_at) -> str | None:
    """Stable change-marker from a timestamp, or None if there isn't one."""
    epoch = to_epoch(updated_at)
    return f"t:{epoch}" if epoch is not None else None


def content_key(conv: Conversation) -> str:
    """Fallback change-marker: a short hash of the conversation's title + messages."""
    h = hashlib.sha256()
    h.update((conv.title or "").encode("utf-8"))
    for m in conv.messages:
        h.update(b"\x1f")
        h.update((m.role or "").encode("utf-8"))
        h.update(b"\x1e")
        h.update((m.text or "").encode("utf-8"))
    return "h:" + h.hexdigest()[:16]
