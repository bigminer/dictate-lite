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

:: Check for existing config and capture current model
set "OLD_MODEL_SIZE="
if exist src\config.py (
    for /f "tokens=3 delims='" %%m in ('findstr /c:"MODEL_SIZE = " src\config.py 2^>nul') do set "OLD_MODEL_SIZE=%%m"
    echo.
    echo  Existing configuration found.
    if not "!OLD_MODEL_SIZE!"=="" echo  Current model: !OLD_MODEL_SIZE!
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
    echo.
    echo # Noise reduction: True to filter background noise
    echo # Helps with fans, AC, ambient noise
    echo NOISE_REDUCTION = False
    echo.
    echo # Copy transcribed text to clipboard in addition to typing
    echo USE_CLIPBOARD = True
) > src\config.py

:: Check if model changed and needs download
set "MODEL_CHANGED=0"
if not "!OLD_MODEL_SIZE!"=="" (
    if not "!OLD_MODEL_SIZE!"=="!MODEL_SIZE!" (
        set "MODEL_CHANGED=1"
    )
)

:: If model changed or new install, offer to download now
if "!MODEL_CHANGED!"=="1" (
    echo.
    echo  Model changed from !OLD_MODEL_SIZE! to !MODEL_SIZE!.
    echo  Downloading new model now...
    goto :download_model
)

:: For new installs without existing config
if "!OLD_MODEL_SIZE!"=="" (
    echo.
    echo  Downloading !MODEL_SIZE! model now...
    goto :download_model
)

goto :install_complete

:download_model
echo.
echo ============================================
echo  Downloading Whisper Model: !MODEL_SIZE!
echo ============================================
echo.
echo  This may take a while depending on model size:
echo    tiny ~75MB, base ~150MB, small ~500MB,
echo    medium ~1.5GB, large ~3GB
echo.

:: Suppress HuggingFace warnings and download the model
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "HF_HUB_DISABLE_PROGRESS_BARS=0"
echo  Downloading... this may take several minutes for larger models.
echo  Please wait, do not close this window.
echo.
python -W ignore -c "import warnings; warnings.filterwarnings('ignore'); from faster_whisper import WhisperModel; print('  Loading model...'); m = WhisperModel('!MODEL_SIZE!', device='!DEVICE!', compute_type='!COMPUTE_TYPE!'); print('  Done!')"
if errorlevel 1 (
    echo.
    echo  WARNING: Model download may have failed.
    echo  The model will be downloaded on first run instead.
    echo.
) else (
    echo.
    echo  Model !MODEL_SIZE! downloaded and verified!
)

:install_complete

:: Generate application icon if it doesn't exist
if not exist voice-dictation.ico (
    echo.
    echo  Generating application icon...
    python src\create_icon.py >nul 2>&1
    if exist voice-dictation.ico (
        echo  OK: Icon created
    ) else (
        echo  Note: Icon generation skipped
    )
)

:: Offer to create desktop shortcut if it doesn't exist
if not exist "%USERPROFILE%\Desktop\Voice Dictation.lnk" (
    echo.
    set /p "CREATE_SHORTCUT=Create desktop shortcut? [Y/n]: "
    if /i not "!CREATE_SHORTCUT!"=="n" (
        echo  Creating shortcut...
        powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $shortcut = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Voice Dictation.lnk'); $shortcut.TargetPath = '%~dp0start-dictation.bat'; $shortcut.WorkingDirectory = '%~dp0'; $shortcut.IconLocation = '%~dp0voice-dictation.ico,0'; $shortcut.Description = 'Voice Dictation - Hold hotkey to record, release to transcribe'; $shortcut.Save()"
        if exist "%USERPROFILE%\Desktop\Voice Dictation.lnk" (
            echo  OK: Shortcut created on Desktop
        ) else (
            echo  Note: Could not create shortcut
        )
    )
)
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
echo  To reconfigure later, run install.bat again.
echo  To uninstall, run uninstall.bat
echo.

:: Prompt to start the application
set /p "START_NOW=Start Voice Dictation now? [Y/n]: "
if /i "!START_NOW!"=="n" (
    echo.
    echo  To start later, run: start-dictation.bat
    echo.
    timeout /t 3 >nul
    exit /b 0
)

:: Start the application
echo.
echo  Starting Voice Dictation...
start "" cmd /c start-dictation.bat
exit /b 0
