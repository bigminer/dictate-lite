@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Voice Dictation

set "PYTHON_EXE=python"
set "PYTHONW_EXE=pythonw"
if exist .venv\Scripts\python.exe set "PYTHON_EXE=.venv\Scripts\python.exe"
if exist .venv\Scripts\pythonw.exe set "PYTHONW_EXE=.venv\Scripts\pythonw.exe"
set "START_MODE=default"

if /i "%~1"=="--skip-healthcheck" set "START_MODE=skip_healthcheck"
if /i "%~1"=="--healthcheck-only" set "START_MODE=healthcheck_only"
if not "%~1"=="" (
    if /i not "%~1"=="--skip-healthcheck" if /i not "%~1"=="--healthcheck-only" (
        echo  Unknown option: %~1
        echo  Usage:
        echo    start-dictation.bat
        echo    start-dictation.bat --skip-healthcheck
        echo    start-dictation.bat --healthcheck-only
        goto :end
    )
)

echo.
echo ============================================
echo  Voice Dictation - Startup + Healthcheck
echo ============================================
echo.

if /i "!START_MODE!"=="skip_healthcheck" (
    echo  Skipping healthcheck via --skip-healthcheck.
    goto :launch
)

echo  Running operational healthcheck...
echo.
"%PYTHON_EXE%" src\startup_healthcheck.py --healthcheck-only
set "HEALTHCHECK_EXIT=!ERRORLEVEL!"

if not "!HEALTHCHECK_EXIT!"=="0" (
    echo.
    echo  Healthcheck reported issues.
    if /i "!START_MODE!"=="healthcheck_only" (
        echo  Healthcheck-only mode finished with failure.
        goto :end
    )
    set /p "CONTINUE_START=Launch Voice Dictation anyway? [y/N]: "
    if /i not "!CONTINUE_START!"=="y" (
        echo.
        echo  Launch canceled. Resolve healthcheck issues and retry.
        goto :end
    )
)

if /i "!START_MODE!"=="healthcheck_only" (
    echo.
    echo  Healthcheck-only mode finished successfully. Not launching dictation.
    goto :end
)

:launch
echo.
echo  Starting Voice Dictation in background...
start "" "%PYTHONW_EXE%" src\dictate.py

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

:end
set /p "CLOSE=Press Enter to close this window..."
exit
