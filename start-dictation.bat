@echo off
cd /d "%~dp0"

:: Use pythonw (no console window) from venv or system
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe dictate.py
) else (
    start "" pythonw dictate.py
)
exit
