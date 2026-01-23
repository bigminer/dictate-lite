@echo off
cd /d "%~dp0"
echo %date% %time% Launching >> "%~dp0hook.log"
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe dictate.py
) else (
    start "" pythonw dictate.py
)
