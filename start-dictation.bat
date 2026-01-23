@echo off
cd /d "%~dp0"

:: Use pythonw (no console window) from venv or system
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe src\dictate.py
) else (
    start "" pythonw src\dictate.py
)
exit
