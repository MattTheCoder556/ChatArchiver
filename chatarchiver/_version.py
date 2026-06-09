"""Single source of truth for the app version.

The CI release workflow (.github/workflows/release.yml) OVERWRITES this file with the
build's version (e.g. 0.1.<run>) right before PyInstaller runs, so the frozen exe knows
its own version and can compare it against the latest GitHub Release. In source checkouts
this stays at the base version (source updates via git, not by version compare).
"""
__version__ = "0.1.0"
