"""Entrypoint launched by Windows Task Scheduler for unattended exports (no GUI)."""
from chatarchiver.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
