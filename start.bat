@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (py -3 -B run.py --open-browser) else (python -B run.py --open-browser)
pause
