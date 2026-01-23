# Voice Dictation Tool

> **Windows Only** - This tool uses Windows-specific APIs for global hotkeys and system tray integration. It will not run on macOS or Linux.

Hold a hotkey to record your voice, release to transcribe and type the text into any application.

Uses OpenAI Whisper (via faster-whisper) with GPU acceleration for fast, accurate transcription. Runs quietly in the system tray.

## Features

- **System tray icon** - Green (ready), red (recording), yellow (processing)
- **Configurable hotkey** - Default Alt+F, fully customizable
- **Multiple languages** - English, auto-detect, or 50+ language codes
- **Model selection** - Trade speed vs accuracy (tiny → large)
- **GPU acceleration** - CUDA support for fast transcription
- **Offline capable** - After initial model download

## Prerequisites

### 0. Internet Connection (First Run Only)

The first time you run the tool, it downloads the Whisper speech model.
Model sizes: tiny ~75MB, base ~150MB, small ~500MB, medium ~1.5GB, large ~3GB.
After download, the model is cached and works offline.

### 1. Python 3.11+ (Required)

**Option A - Microsoft Store (easiest):**
1. Open Microsoft Store
2. Search "Python 3.13"
3. Click Install

**Option B - python.org:**
1. Go to https://www.python.org/downloads/
2. Download Python 3.13+
3. Run installer
4. **IMPORTANT:** Check "Add Python to PATH" during installation

**Option C - winget:**
```powershell
winget install Python.Python.3.13
```

### 2. NVIDIA GPU + CUDA (Optional, but recommended)

For fast transcription, you need an NVIDIA GPU with CUDA support. Typical transcription times vary by audio length and hardware (GPU: ~1-3 seconds for short phrases, CPU: ~5-15 seconds).

**Check if you have an NVIDIA GPU:**
1. Press `Win+X` → Device Manager
2. Expand "Display adapters"
3. Look for "NVIDIA GeForce..." or "NVIDIA RTX..."

**If you have NVIDIA GPU, install CUDA Toolkit:**
1. Go to https://developer.nvidia.com/cuda-downloads
2. Select Windows → x86_64 → 11 → exe (local)
3. Download and install (use Express installation)
4. Restart your computer

**No NVIDIA GPU?** The tool will automatically use CPU mode. It's slower but works fine.

## Installation

1. **Run the installer:**
   ```
   Double-click: install.bat
   ```

2. **Follow the prompts:**
   - Choose your hotkey (default: Alt+F)
   - Select model size (tiny/base/small/medium/large)
   - Select language (English/auto-detect/other)

3. **Verify installation:**
   ```
   Double-click: test-install.bat
   ```

The installer is idempotent - safe to run multiple times to reconfigure.

## Usage

1. **Start the tool:**
   ```
   Double-click: start-dictation.bat
   ```
   A colored circle appears in your system tray.

2. **Dictate:**
   - Hold your hotkey (default: Alt+F)
   - Icon turns red - speak clearly
   - Release the hotkey
   - Icon turns yellow while processing
   - Text appears in your active window
   - Icon returns to green

   **Note:** Text is also copied to your clipboard as a backup. You can paste (Ctrl+V) if text injection fails in certain applications. Disable with `USE_CLIPBOARD = False` in config.py.

3. **Check status:** Hover over tray icon for current settings

4. **Exit:** Right-click tray icon → Exit

## Limitations

This tool is optimized for single-speaker dictation in reasonably quiet environments. Transcription accuracy may degrade in the following scenarios:

- **Background conversations** - Multiple voices speaking simultaneously
- **Noisy environments** - Loud ambient noise, machinery, or music
- **Distant microphone placement** - Speaking far from the microphone

For best results, use a close-range microphone and minimize background noise. If you experience issues with ambient noise, enable noise reduction in `config.py`:

```python
NOISE_REDUCTION = True
```

This applies audio filtering before transcription, which can help with stationary noise (fans, AC, traffic hum) but may not fully isolate your voice from other speakers.

### Text Injection Behavior

Transcribed text is injected into the active window using simulated keystrokes with a 10ms delay between characters. This deliberate throttling prevents crashes in certain terminal applications (notably Claude Code's TUI). The text is also copied to the clipboard by default as a backup.

If clipboard copying interferes with your workflow, disable it in `config.py`:

```python
USE_CLIPBOARD = False
```

## Uninstalling

Run `uninstall.bat` to remove:
- Virtual environment
- Configuration
- Log files
- Optionally: downloaded model cache (~500MB-3GB)

## Troubleshooting

### "Python not found"
- Install Python (see Prerequisites above)
- Make sure "Add to PATH" was checked during installation
- Try restarting your computer

### "Access denied" or hotkey doesn't work
- Right-click `start-dictation.bat` → "Run as administrator"

### Transcription is slow
- You're probably in CPU mode
- Install NVIDIA drivers and CUDA toolkit (see Prerequisites)
- Re-run `install.bat` to reconfigure

### "No microphone found" or "No audio captured"
- Check your microphone is connected
- Windows Settings → Sound → Input → Make sure correct mic is selected
- Windows Settings → Privacy → Microphone → Allow apps to access microphone
- Try unplugging and replugging USB microphones

### Model download fails or hangs
- Check your internet connection
- If behind a corporate proxy, you may need to configure proxy settings
- The model downloads to `%USERPROFILE%\.cache\huggingface` - ensure you have enough free space
- Try again later if HuggingFace servers are slow

### CUDA/GPU errors at runtime
- Make sure CUDA Toolkit is installed (not just NVIDIA drivers)
- Restart your computer after installing CUDA
- Re-run `install.bat` to reconfigure

### Tray icon doesn't appear
- Check the system tray overflow (^ arrow near clock)
- Some systems hide new tray icons by default

## Configuration

After installation, edit `config.py` or re-run `install.bat`:

```python
HOTKEY = 'alt+f'          # Your recording hotkey
MODEL_SIZE = 'small'      # tiny, base, small, medium, large
LANGUAGE = 'en'           # 'en', 'auto', 'es', 'fr', 'de', 'ja', etc.
DEVICE = 'cuda'           # 'cuda' or 'cpu'
COMPUTE_TYPE = 'float16'  # 'float16' for GPU, 'int8' for CPU
AUDIO_DEVICE = None       # None = default, or device index
NOISE_REDUCTION = False   # True to filter background noise
USE_CLIPBOARD = True      # Copy text to clipboard as backup
```

## Files

| File | Purpose |
|------|---------|
| `dictate.py` | Main application - system tray, hotkey, transcription |
| `speak.py` | Text-to-speech utility (see below) |
| `install.bat` | Setup wizard (safe to re-run) |
| `uninstall.bat` | Remove installation |
| `start-dictation.bat` | Launch the tool |
| `launch.cmd` | Headless launcher with logging |
| `test-install.bat` | Verify installation |
| `config.py` | Your settings (generated) |
| `config.example.py` | Configuration template |

### speak.py - Text-to-Speech Utility

A standalone utility for text-to-speech using Microsoft Edge's neural voices:

```
.venv\Scripts\python speak.py "Hello, this is a test."
```

This is a separate tool from the main dictation functionality and is included for convenience.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

This project is built on excellent open source software:

- **[OpenAI Whisper](https://github.com/openai/whisper)** - Speech recognition model (MIT)
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** - Optimized Whisper implementation (MIT)
- **[pystray](https://github.com/moses-palmer/pystray)** - System tray integration (LGPLv3)
- **[Pillow](https://python-pillow.org/)** - Icon generation (HPND)
- **[keyboard](https://github.com/boppreh/keyboard)** - Global hotkeys (MIT)
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** - Audio capture (MIT)
- **[noisereduce](https://github.com/timsainb/noisereduce)** - Audio noise reduction (MIT)
- **[edge-tts](https://github.com/rany2/edge-tts)** - Text-to-speech (MIT)
