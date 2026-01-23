@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Voice Dictation

echo.
echo ============================================
echo  Voice Dictation - Starting
echo ============================================
echo.

:: Use pythonw (no console window) from venv or system
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe src\dictate.py
) else (
    start "" pythonw src\dictate.py
)

echo  Voice Dictation is now running in the background.
echo.
echo  Look for the colored circle icon in your system tray
echo  (bottom-right corner of your screen, near the clock).
echo.
echo  Icon colors:
echo    Gray   = Loading model
echo    Green  = Ready (hold hotkey to record)
echo    Red    = Recording
echo    Yellow = Processing transcription
echo.
echo  To STOP the application:
echo    Right-click the system tray icon and select "Quit"
echo.
echo ============================================
echo.

set /p "CLOSE=Press Enter to close this window..."
exit
