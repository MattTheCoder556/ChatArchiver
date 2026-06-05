# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Chat Archiver (one-folder, windowed).

The hard part of packaging this app is Patchright/Playwright: they ship a Node-based
driver and data files that must travel with the exe. collect_all() pulls those in.

Build:  pyinstaller --noconfirm ChatArchiver.spec
Output: dist/ChatArchiver/ChatArchiver.exe
"""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for _pkg in ("patchright", "playwright"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ChatArchiver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed: no console flashes for GUI or scheduled run
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ChatArchiver',
)
