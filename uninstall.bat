@echo off
setlocal enabledelayedexpansion
title Voice Dictation - Uninstall
cd /d "%~dp0"

echo ============================================
echo  Voice Dictation - Uninstall
echo ============================================
echo.
echo  This will remove:
echo    - Virtual environment (.venv folder)
echo    - Configuration file (config.py)
echo    - Log files (*.log)
echo.
echo  Optionally:
echo    - Downloaded Whisper models (~500MB-3GB)
echo      Location: %USERPROFILE%\.cache\huggingface
echo.
echo  This will NOT remove:
echo    - The application files themselves
echo    - Python installation
echo.

set /p "CONFIRM=Are you sure you want to uninstall? [y/N]: "
if /i not "!CONFIRM!"=="y" (
    echo.
    echo  Uninstall cancelled.
    pause
    exit /b 0
)

echo.
echo ============================================
echo  Removing local files...
echo ============================================

:: Remove virtual environment
if exist .venv (
    echo  Removing .venv...
    rmdir /s /q .venv
    echo    OK: .venv removed
) else (
    echo    .venv not found, skipping
)

:: Remove config file
if exist config.py (
    echo  Removing config.py...
    del /f config.py
    echo    OK: config.py removed
) else (
    echo    config.py not found, skipping
)

:: Remove log files
echo  Removing log files...
if exist *.log (
    del /f *.log
    echo    OK: Log files removed
) else (
    echo    No log files found
)

:: Remove __pycache__
if exist __pycache__ (
    echo  Removing __pycache__...
    rmdir /s /q __pycache__
    echo    OK: __pycache__ removed
)

echo.
echo ============================================
echo  Model Cache
echo ============================================
echo.
echo  Downloaded Whisper models are stored in:
echo    %USERPROFILE%\.cache\huggingface\hub
echo.
echo  These models can be 500MB to 3GB+ and are shared
echo  with other applications that use HuggingFace models.
echo.

set "CACHE_DIR=%USERPROFILE%\.cache\huggingface\hub"

:: Check if cache exists and estimate size
if exist "!CACHE_DIR!" (
    echo  Checking cache size...
    for /f "tokens=3" %%a in ('dir "!CACHE_DIR!" /s 2^>nul ^| findstr "File(s)"') do set "CACHE_SIZE=%%a"
    echo  Cache folder exists ^(may contain other models too^)
    echo.
    echo  WARNING: Deleting the HuggingFace cache will remove ALL
    echo           downloaded models, not just Whisper models.
    echo           Other applications may need to re-download their models.
    echo.
    set /p "DELETE_CACHE=Delete model cache? [y/N]: "
    if /i "!DELETE_CACHE!"=="y" (
        echo.
        echo  Removing HuggingFace cache...
        rmdir /s /q "!CACHE_DIR!"
        echo    OK: Model cache removed
    ) else (
        echo    Keeping model cache
    )
) else (
    echo  No model cache found at !CACHE_DIR!
)

echo.
echo ============================================
echo  Uninstall Complete
echo ============================================
echo.
echo  The following were removed:
echo    - Virtual environment
echo    - Configuration
echo    - Log files
if /i "!DELETE_CACHE!"=="y" (
    echo    - Model cache
)
echo.
echo  Application files remain in this folder.
echo  To completely remove, delete this folder manually.
echo.
pause
