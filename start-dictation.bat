@echo off
title Voice Dictation
cd /d "%~dp0"

:: Use venv if it exists, otherwise use system Python
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

python dictate.py
pause
