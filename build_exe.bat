@echo off
cd /d "%~dp0"
echo === Building Chat Archiver.exe ===
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m patchright install chromium
echo.
echo Running PyInstaller (this takes a few minutes)...
python -m PyInstaller --noconfirm ChatArchiver.spec
echo.
echo Done. Your app is here:
echo     dist\ChatArchiver\ChatArchiver.exe
echo Copy the whole dist\ChatArchiver folder to use it.
pause
