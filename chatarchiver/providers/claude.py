"""Claude (claude.ai) exporter.

Flow: /api/organizations -> pick org -> /api/organizations/{org}/chat_conversations
(cheap list, carries updated_at), then each conversation with
?tree=True&rendering_mode=raw.

Private/unversioned endpoints — fix here if Anthropic changes them.
"""
from __future__ import annotations

from datetime import datetime

from .base import ConvMeta, Provider
from ..models import Conversation, Message

_ORG_JS = (
    "async () => { const r = await fetch('/api/organizations', {credentials:'include'});"
    " return r.ok ? await r.json() : null; }"
)

_LIST_JS = r"""
async (orgId) => {
  const r = await fetch(`/api/organizations/${orgId}/chat_conversations`, {credentials:'include'});
  if (!r.ok) return {error: 'list-' + r.status};
  return {items: await r.json()};
}
"""

_CONV_JS = r"""
async ([orgId, convId]) => {
  const r = await fetch(`/api/organizations/${orgId}/chat_conversations/${convId}?tree=True&rendering_mode=raw`,
                        {credentials:'include'});
  if (!r.ok) return {error: 'conv-' + r.status};
  return await r.json();
}
"""


def _parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class ClaudeProvider(Provider):
    id = "claude"
    label = "Claude (Anthropic)"
    home_url = "https://claude.ai/"

    def __init__(self):
        self._org: str | None = None

    def _org_id(self, page) -> str | None:
        orgs = page.evaluate(_ORG_JS)
        if not orgs:
            return None
        return orgs[0].get("uuid")

    def check_auth(self, page) -> bool:
        try:
            return bool(self._org_id(page))
        except Exception:
            return False

    def list_conversations(self, page) -> list[ConvMeta]:
        org = self._org_id(page)
        if not org:
            raise RuntimeError("Claude: not logged in (no organization found)")
        self._org = org
        data = page.evaluate(_LIST_JS, org)
        if data.get("error"):
            raise RuntimeError(f"Claude list failed: {data['error']}")
        return [ConvMeta(id=it["uuid"], title=it.get("name") or "Untitled",
                         created_at=_parse_ts(it.get("created_at")),
                         updated_at=_parse_ts(it.get("updated_at")))
                for it in (data.get("items") or [])]

    def fetch_one(self, page, meta: ConvMeta) -> Conversation | None:
        org = self._org or self._org_id(page)
        if not org:
            return None
        raw = page.evaluate(_CONV_JS, [org, meta.id])
        if not raw or raw.get("error"):
            return None
        return Conversation(
            provider=self.id, id=meta.id, title=meta.title,
            created_at=meta.created_at, updated_at=meta.updated_at, messages=_parse(raw))


def _parse(raw: dict) -> list[Message]:
    out: list[Message] = []
    for m in raw.get("chat_messages") or []:
        role = "user" if m.get("sender") == "human" else "assistant"
        text = ""
        if m.get("content"):
            blocks = [b["text"] for b in m["content"] if b.get("type") == "text" and b.get("text")]
            text = "\n\n".join(blocks)
        if not text:
            text = m.get("text") or ""
        if text.strip():
            out.append(Message(role=role, text=text, created_at=_parse_ts(m.get("created_at"))))
    return out
