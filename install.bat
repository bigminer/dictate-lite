@echo off
setlocal enabledelayedexpansion
title Voice Dictation - Install
cd /d "%~dp0"

echo ============================================
echo  Voice Dictation - Installation
echo ============================================
echo.

:: Check for Python
echo [1/6] Checking for Python...
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

echo [2/6] Setting up virtual environment...
if exist .venv (
    echo       .venv already exists
    set /p "RECREATE=Recreate virtual environment? [y/N]: "
    if /i "!RECREATE!"=="y" (
        echo       Removing old .venv...
        rmdir /s /q .venv
        python -m venv .venv
        echo       OK: .venv recreated
    ) else (
        echo       OK: Using existing .venv
    )
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo       FAIL: Could not create virtual environment
        pause
        exit /b 1
    )
    echo       OK: .venv created
)

echo [3/6] Activating virtual environment...
call .venv\Scripts\activate.bat
echo       OK: Activated

echo [4/6] Installing dependencies...
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
echo [5/6] Checking GPU support...
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
echo  [6/6] Configuration
echo ============================================

:: Check for existing config
if exist config.py (
    echo.
    echo  Existing configuration found.
    set /p "RECONFIG=Reconfigure settings? [y/N]: "
    if /i not "!RECONFIG!"=="y" (
        echo  Keeping existing configuration.
        goto :install_complete
    )
)

echo.
echo  --- Hotkey ---
echo  The hotkey is what you hold to record.
echo  Default: alt+f
echo.
echo  AVOID these ^(they conflict with Windows/apps^):
echo    ctrl+c, ctrl+v, ctrl+x, ctrl+z, ctrl+s
echo    alt+tab, alt+f4, ctrl+alt+del
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

echo.
echo  --- Model Size ---
echo  Larger models are more accurate but slower and use more VRAM.
echo.
echo    1. tiny   - Fastest, ~1GB VRAM, less accurate
echo    2. base   - Fast, ~1GB VRAM, good accuracy
echo    3. small  - Balanced, ~2GB VRAM ^(recommended^)
echo    4. medium - Slower, ~5GB VRAM, better accuracy
echo    5. large  - Slowest, ~10GB VRAM, best accuracy
echo.

set "MODEL_SIZE=small"
set /p "MODEL_CHOICE=Choose model [1-5, default=3]: "
if "!MODEL_CHOICE!"=="1" set "MODEL_SIZE=tiny"
if "!MODEL_CHOICE!"=="2" set "MODEL_SIZE=base"
if "!MODEL_CHOICE!"=="3" set "MODEL_SIZE=small"
if "!MODEL_CHOICE!"=="4" set "MODEL_SIZE=medium"
if "!MODEL_CHOICE!"=="5" set "MODEL_SIZE=large"

echo.
echo  --- Language ---
echo  Language for transcription.
echo.
echo    1. English (en) - default
echo    2. Auto-detect (auto) - slower but works with any language
echo    3. Spanish (es)
echo    4. French (fr)
echo    5. German (de)
echo    6. Other (enter code)
echo.

set "LANGUAGE=en"
set /p "LANG_CHOICE=Choose language [1-6, default=1]: "
if "!LANG_CHOICE!"=="1" set "LANGUAGE=en"
if "!LANG_CHOICE!"=="2" set "LANGUAGE=auto"
if "!LANG_CHOICE!"=="3" set "LANGUAGE=es"
if "!LANG_CHOICE!"=="4" set "LANGUAGE=fr"
if "!LANG_CHOICE!"=="5" set "LANGUAGE=de"
if "!LANG_CHOICE!"=="6" (
    set /p "LANGUAGE=Enter language code: "
)

:: Determine device setting
if "!USE_CPU!"=="1" (
    set "DEVICE=cpu"
    set "COMPUTE_TYPE=int8"
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
    echo MODEL_SIZE = '!MODEL_SIZE!'
    echo.
    echo # Language for transcription
    echo # 'en' = English, 'auto' = auto-detect, or specific code
    echo LANGUAGE = '!LANGUAGE!'
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

:install_complete
echo.
echo ============================================
echo  Installation Complete!
echo ============================================
echo.
echo  Your settings:
echo    Hotkey:   !HOTKEY! ^(hold to record, release to transcribe^)
echo    Model:    !MODEL_SIZE!
echo    Language: !LANGUAGE!
echo    Device:   !DEVICE!
echo.
echo  IMPORTANT - First Run:
echo    The first time you run the tool, it will download the
echo    Whisper speech model. This requires internet access.
echo    Model sizes: tiny ~75MB, base ~150MB, small ~500MB,
echo                 medium ~1.5GB, large ~3GB
echo.
echo  Next steps:
echo    1. Run test-install.bat to verify everything works
echo    2. Run start-dictation.bat to use the tool
echo.
echo  To reconfigure later, run install.bat again.
echo  To uninstall, run uninstall.bat
echo.
pause
