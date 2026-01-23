@echo off
setlocal enabledelayedexpansion
title Voice Dictation - Installation Test
cd /d "%~dp0"

echo ============================================
echo  Voice Dictation - Installation Test
echo ============================================
echo.

set "ERRORS=0"

:: Check for venv
echo [1/5] Checking virtual environment...
if exist .venv\Scripts\activate.bat (
    echo       OK: .venv exists
    call .venv\Scripts\activate.bat
) else (
    echo       FAIL: .venv not found. Run install.bat first.
    set /a ERRORS+=1
)

:: Check for config
echo [2/5] Checking configuration...
if exist src\config.py (
    echo       OK: src\config.py exists
) else (
    echo       FAIL: src\config.py not found. Run install.bat first.
    set /a ERRORS+=1
)

:: Check Python imports
echo [3/5] Testing Python imports...
python -c "import faster_whisper; print('       OK: faster_whisper')" 2>nul || (
    echo       FAIL: faster_whisper not installed
    set /a ERRORS+=1
)
python -c "import keyboard; print('       OK: keyboard')" 2>nul || (
    echo       FAIL: keyboard not installed
    set /a ERRORS+=1
)
python -c "import sounddevice; print('       OK: sounddevice')" 2>nul || (
    echo       FAIL: sounddevice not installed
    set /a ERRORS+=1
)
python -c "import numpy; print('       OK: numpy')" 2>nul || (
    echo       FAIL: numpy not installed
    set /a ERRORS+=1
)

:: Check CUDA availability
echo [4/5] Testing CUDA/GPU support...
python -c "import torch; cuda=torch.cuda.is_available(); print(f'       {\"OK: CUDA available\" if cuda else \"WARN: CUDA not available (CPU mode)\"}')" 2>nul || (
    echo       INFO: torch not installed, checking nvidia-smi...
    nvidia-smi >nul 2>&1 && (
        echo       OK: NVIDIA GPU detected
    ) || (
        echo       WARN: No NVIDIA GPU detected - will use CPU mode
    )
)

:: Check audio devices
echo [5/5] Testing audio input...
python -c "import sounddevice as sd; devs=[d for d in sd.query_devices() if d['max_input_channels']>0]; print(f'       OK: {len(devs)} input device(s) found'); print(f'       Default: {sd.query_devices(sd.default.device[0])[\"name\"]}')" 2>nul || (
    echo       FAIL: Could not query audio devices
    set /a ERRORS+=1
)

echo.
echo ============================================
if !ERRORS! EQU 0 (
    echo  All checks passed!
    echo  Run start-dictation.bat to use the tool.
) else (
    echo  !ERRORS! error(s) found.
    echo  Please fix the issues above and re-run install.bat
)
echo ============================================
echo.
pause
