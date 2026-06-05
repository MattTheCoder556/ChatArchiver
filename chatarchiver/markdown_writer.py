"""Turn a Conversation into a clean Markdown file with YAML front-matter."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Conversation, to_epoch

_UNSAFE = re.compile(r"[^\w\- ]+")
_ROLE_LABELS = {"user": "You", "assistant": "Assistant", "system": "System", "tool": "Tool"}


def _slug(title: str, maxlen: int = 60) -> str:
    title = (title or "untitled").strip()
    title = _UNSAFE.sub("", title).strip().replace(" ", "-")
    title = re.sub(r"-{2,}", "-", title).strip("-")
    return (title[:maxlen] or "untitled").strip("-")


def _fmt_ts(ts) -> str:
    epoch = to_epoch(ts)
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_conversation(conv: Conversation, out_root: Path) -> Path:
    """Write one conversation to {out_root}/{provider}/{date}_{slug}_{shortid}.md."""
    folder = out_root / conv.provider
    folder.mkdir(parents=True, exist_ok=True)

    dt = conv.created_dt()
    date_prefix = dt.strftime("%Y-%m-%d") if dt else "undated"
    short = conv.id.replace("-", "")[:8] or "noid"
    path = folder / f"{date_prefix}_{_slug(conv.title)}_{short}.md"

    safe_title = (conv.title or "Untitled").replace('"', "'")
    lines: list[str] = ["---", f'title: "{safe_title}"', f"provider: {conv.provider}",
                        f"conversation_id: {conv.id}"]
    if conv.created_at:
        lines.append(f"created: {_fmt_ts(conv.created_at)}")
    if conv.updated_at:
        lines.append(f"updated: {_fmt_ts(conv.updated_at)}")
    lines += [f"messages: {len(conv.messages)}", "---", "", f"# {conv.title or 'Untitled'}", ""]

    for m in conv.messages:
        lines.append(f"## {_ROLE_LABELS.get(m.role, m.role.title())}")
        ts = _fmt_ts(m.created_at)
        if ts:
            lines += [f"*{ts}*", ""]
        lines += [(m.text.rstrip() if m.text else "_(empty)_"), ""]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
