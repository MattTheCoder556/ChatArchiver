"""ChatGPT (chatgpt.com) exporter.

Uses the site's own backend-api. After login, /api/auth/session yields a short-lived
accessToken; we page through /backend-api/conversations (cheap list, carries
update_time) and fetch each conversation tree from /backend-api/conversation/{id}.

These endpoints are private and unversioned — if OpenAI changes them this is the file
to fix. Everything brittle is isolated here.
"""
from __future__ import annotations

from .base import ConvMeta, Provider
from ..models import Conversation, Message

_LIST_JS = r"""
async () => {
  const sess = await fetch('/api/auth/session', {credentials:'include'})
                 .then(r => r.ok ? r.json() : null).catch(() => null);
  if (!sess || !sess.accessToken) return {error: 'no-auth'};
  const token = sess.accessToken;
  const headers = {'Authorization': 'Bearer ' + token};
  let items = [];
  const limit = 100;
  for (let offset = 0; offset < 20000; offset += limit) {
    const r = await fetch(`/backend-api/conversations?offset=${offset}&limit=${limit}&order=updated`,
                          {headers, credentials: 'include'});
    if (!r.ok) return {error: 'list-' + r.status};
    const d = await r.json();
    const batch = d.items || [];
    items = items.concat(batch);
    if (batch.length < limit) break;
  }
  return {token, items: items.map(c => ({
    id: c.id, title: c.title, create_time: c.create_time, update_time: c.update_time}))};
}
"""

_CONV_JS = r"""
async ([id, token]) => {
  const r = await fetch('/backend-api/conversation/' + id,
                        {headers: {'Authorization': 'Bearer ' + token}, credentials: 'include'});
  if (!r.ok) return {error: 'conv-' + r.status};
  return await r.json();
}
"""

_AUTH_JS = (
    "async () => { const r = await fetch('/api/auth/session', {credentials:'include'});"
    " if (!r.ok) return false; const j = await r.json(); return !!(j && j.accessToken); }"
)

_TOKEN_JS = (
    "async () => { const r = await fetch('/api/auth/session', {credentials:'include'});"
    " if (!r.ok) return null; const j = await r.json(); return j.accessToken || null; }"
)


class ChatGPTProvider(Provider):
    id = "chatgpt"
    label = "ChatGPT (OpenAI)"
    home_url = "https://chatgpt.com/"

    def __init__(self):
        self._token: str | None = None

    def check_auth(self, page) -> bool:
        try:
            return bool(page.evaluate(_AUTH_JS))
        except Exception:
            return False

    def list_conversations(self, page) -> list[ConvMeta]:
        data = page.evaluate(_LIST_JS)
        if not data or data.get("error"):
            raise RuntimeError(f"ChatGPT list failed: {data.get('error') if data else 'no response'}")
        self._token = data["token"]
        return [ConvMeta(id=it["id"], title=it.get("title") or "Untitled",
                         created_at=it.get("create_time"), updated_at=it.get("update_time"))
                for it in data["items"]]

    def fetch_one(self, page, meta: ConvMeta) -> Conversation | None:
        token = self._token or page.evaluate(_TOKEN_JS)
        self._token = token
        if not token:
            return None
        raw = page.evaluate(_CONV_JS, [meta.id, token])
        if not raw or raw.get("error"):
            return None
        return Conversation(
            provider=self.id, id=meta.id, title=meta.title,
            created_at=meta.created_at, updated_at=meta.updated_at, messages=_parse(raw))


def _parse(raw: dict) -> list[Message]:
    """Walk the message tree from current_node back to the root, then linearise."""
    mapping = raw.get("mapping") or {}
    chain, node_id, seen = [], raw.get("current_node"), set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        node = mapping.get(node_id)
        if not node:
            break
        chain.append(node)
        node_id = node.get("parent")
    chain.reverse()

    out: list[Message] = []
    for node in chain:
        m = node.get("message")
        if not m:
            continue
        role = (m.get("author") or {}).get("role")
        if role not in ("user", "assistant", "tool"):
            continue
        text = _content_text(m.get("content") or {})
        if text.strip():
            out.append(Message(role=role, text=text, created_at=m.get("create_time")))
    return out


def _content_text(content: dict) -> str:
    if content.get("content_type") == "text":
        return "\n".join(content.get("parts") or [])
    out = []
    for p in content.get("parts") or []:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and p.get("text"):
            out.append(p["text"])   # multimodal: keep text, skip image blobs
    return "\n".join(out)
