@echo off
cd /d "%~dp0"
echo Installing Chat Archiver...
python -m pip install -r requirements.txt
echo.
echo Installing Firefox for Playwright (no Chrome, no Google binary)...
python -m playwright install firefox
echo.
echo Done. Double-click start.bat to open Chat Archiver.
pause
