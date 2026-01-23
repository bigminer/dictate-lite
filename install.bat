@echo off
setlocal enabledelayedexpansion
title Voice Dictation - Install
cd /d "%~dp0"

echo ============================================
echo  Voice Dictation - Installation
echo ============================================
echo.

:: Check for Python
echo [1/5] Checking for Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found!
    echo.
    echo  Please install Python 3.11+ using ONE of these methods:
    echo.
    echo  Option A - Microsoft Store ^(easiest^):
    echo    1. Open Microsoft Store
    echo    2. Search "Python 3.13"
    echo    3. Click Install
    echo.
    echo  Option B - Download from python.org:
    echo    1. Go to https://www.python.org/downloads/
    echo    2. Download Python 3.13+
    echo    3. Run installer
    echo    4. IMPORTANT: Check "Add Python to PATH"
    echo.
    echo  Option C - Using winget ^(PowerShell^):
    echo    winget install Python.Python.3.13
    echo.
    echo  After installing Python, run this script again.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo       OK: Python %PYVER% found

:: Check Python version is 3.11+
python -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if errorlevel 1 (
    echo.
    echo  ERROR: Python 3.11+ required, but you have %PYVER%
    echo  Please install a newer version of Python.
    echo.
    pause
    exit /b 1
)

echo [2/5] Creating virtual environment...
if exist .venv (
    echo       .venv already exists, skipping creation
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo       FAIL: Could not create virtual environment
        pause
        exit /b 1
    )
    echo       OK: .venv created
)

echo [3/5] Activating virtual environment...
call .venv\Scripts\activate.bat
echo       OK: Activated

echo [4/5] Installing dependencies...
echo       This may take a few minutes...
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to install dependencies.
    echo  Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)
echo       OK: Dependencies installed

echo.
echo [5/5] Checking GPU support...
echo.

:: Check for NVIDIA GPU via nvidia-smi
set "USE_CPU=0"
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo  No NVIDIA GPU detected.
    set "USE_CPU=1"
) else (
    echo  NVIDIA GPU detected!
    echo.
    echo  Checking CUDA availability...

    :: Try to verify CUDA actually works with faster-whisper
    python -c "from faster_whisper import WhisperModel; m=WhisperModel('tiny',device='cuda',compute_type='float16')" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  WARNING: GPU detected but CUDA is not working.
        echo           This usually means CUDA Toolkit is not installed.
        echo.
        echo  To enable GPU acceleration:
        echo    1. Go to https://developer.nvidia.com/cuda-downloads
        echo    2. Download and install CUDA Toolkit
        echo    3. Restart your computer
        echo    4. Run install.bat again
        echo.
        echo  Continuing with CPU mode for now...
        set "USE_CPU=1"
    ) else (
        echo  CUDA is working! GPU acceleration enabled.
        set "USE_CPU=0"
    )
)

echo.
echo ============================================
echo  Hotkey Configuration
echo ============================================
echo.
echo  The hotkey is what you hold to record.
echo  Default: alt+f
echo.
echo  AVOID these ^(they conflict with Windows/apps^):
echo    ctrl+c, ctrl+v, ctrl+x, ctrl+z, ctrl+s
echo    alt+tab, alt+f4, ctrl+alt+del
echo.
echo  GOOD alternatives:
echo    alt+f, ctrl+shift+d, ctrl+alt+r, alt+`
echo.

set "HOTKEY=alt+f"
set /p "USER_HOTKEY=Enter hotkey [alt+f]: "
if not "!USER_HOTKEY!"=="" set "HOTKEY=!USER_HOTKEY!"

:: Warn about potentially problematic hotkeys
echo !HOTKEY! | findstr /i "ctrl+c ctrl+v ctrl+x ctrl+z ctrl+s ctrl+a alt+tab alt+f4" >nul
if not errorlevel 1 (
    echo.
    echo  WARNING: '!HOTKEY!' conflicts with common shortcuts!
    set /p "CONFIRM=Use anyway? [y/N]: "
    if /i not "!CONFIRM!"=="y" (
        set "HOTKEY=alt+f"
        echo  Using default: alt+f
    )
)

:: Determine device setting
if "!USE_CPU!"=="1" (
    set "DEVICE=cpu"
    set "COMPUTE_TYPE=int8"
    echo.
    echo  Note: Using CPU mode. Transcription will be slower.
) else (
    set "DEVICE=cuda"
    set "COMPUTE_TYPE=float16"
)

:: Write config.py
echo.
echo Writing configuration...
(
    echo # Voice Dictation Configuration
    echo # Edit this file to change settings, or re-run install.bat
    echo.
    echo # Hotkey to hold for recording ^(release to transcribe^)
    echo HOTKEY = '!HOTKEY!'
    echo.
    echo # Whisper model size: tiny, base, small, medium, large
    echo # Larger = more accurate but slower
    echo MODEL_SIZE = 'small'
    echo.
    echo # Device: 'cuda' for GPU, 'cpu' for CPU-only
    echo DEVICE = '!DEVICE!'
    echo.
    echo # Compute type: 'float16' for GPU, 'int8' for CPU
    echo COMPUTE_TYPE = '!COMPUTE_TYPE!'
    echo.
    echo # Audio device index: None = system default
    echo AUDIO_DEVICE = None
) > config.py

echo.
echo ============================================
echo  Installation Complete!
echo ============================================
echo.
echo  Your settings:
echo    Hotkey: !HOTKEY! ^(hold to record, release to transcribe^)
echo    Device: !DEVICE!
echo    Model:  small
echo.
echo  IMPORTANT - First Run:
echo    The first time you run the tool, it will download the
echo    Whisper speech model (~500MB). This requires internet.
echo    Subsequent runs use the cached model and work offline.
echo.
echo  Next steps:
echo    1. Run test-install.bat to verify everything works
echo    2. Run start-dictation.bat to use the tool
echo.
echo  Tip: If hotkeys don't work, try "Run as administrator"
echo.
pause
