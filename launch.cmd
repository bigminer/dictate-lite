@echo off
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe src\dictate.py
) else (
    start "" pythonw src\dictate.py
)
