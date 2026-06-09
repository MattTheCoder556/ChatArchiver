"""Chat Archiver — export your ChatGPT / Claude chat history to plain Markdown."""

try:
    from ._version import __version__
except Exception:                       # pragma: no cover - defensive only
    __version__ = "0.0.0"
