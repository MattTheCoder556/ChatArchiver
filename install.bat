@echo off
cd /d "%~dp0"
echo Installing Chat Archiver...
python -m pip install -r requirements.txt
echo.
echo Installing the patched browser (this defeats the captcha)...
python -m patchright install chromium
echo.
echo Done. Double-click start.bat to open Chat Archiver.
pause
