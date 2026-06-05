#!/usr/bin/env bash
# Linux build of Chat Archiver (run this ON a Linux machine — PyInstaller can't
# cross-compile from Windows). Produces dist/ChatArchiver/ChatArchiver (an ELF binary).
#
# Note: on Linux the scheduler module is Windows-only (it uses schtasks). Automatic
# scheduling on Linux would use cron/systemd timers — not wired up yet. The GUI, login,
# export and incremental features all work.
set -e
cd "$(dirname "$0")"
echo "=== Building Chat Archiver (Linux) ==="
python3 -m pip install -r requirements.txt
python3 -m pip install pyinstaller
python3 -m patchright install chromium
python3 -m PyInstaller --noconfirm ChatArchiver.spec
echo
echo "Done -> dist/ChatArchiver/ChatArchiver"
