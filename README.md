# Voice Dictation Tool

> **Windows Only** - This tool uses Windows-specific APIs for global hotkeys and system tray integration. It will not run on macOS or Linux.

Hold a hotkey to record your voice, release to transcribe and type the text into any application. Or enable **Open Mic Mode** for hands-free dictation with wake word activation.

Uses OpenAI Whisper (via faster-whisper) with GPU acceleration for fast, accurate transcription. Runs quietly in the system tray.

## Features

- **Push-to-talk** - Hold hotkey to record, release to transcribe
- **Native hotkey detection** - Uses Win32 `RegisterHotKey` (not a keyboard hook), so detection survives lock/unlock and sleep, never suppresses other keystrokes, and cannot stall the keyboard
- **Open Mic Mode** - Say a wake word to start recording; silence ends the segment automatically
- **System tray icon** - Green (ready), blue (open mic listening), red (recording), yellow (processing), orange (degraded - a failure needs attention)
- **Loud failure feedback** - Error tone + orange tray icon when hotkey detection or audio recovery fails, instead of failing silently
- **Audio feedback** - Ascending/descending tones confirm recording start/stop in both hotkey and open mic modes
- **Configurable hotkey** - Default Alt+F, fully customizable
- **Multiple languages** - English, auto-detect, or 50+ language codes
- **Model selection** - Trade speed vs accuracy (tiny → large, plus large-v3-turbo for near-large accuracy at higher speed)
- **GPU acceleration** - CUDA support for fast transcription
- **Offline capable** - After initial model download

## Prerequisites

### 0. Internet Connection (First Run Only)

The first time you run the tool, it downloads the Whisper speech model.
Model sizes: tiny ~75MB, base ~150MB, small ~500MB, medium ~1.5GB, large-v3-turbo ~1.6GB, large ~3GB.
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
   A startup healthcheck opens in the command window:
   - Validates microphone stream health
   - Prompts you to say **"check 1 2 3"**
   - Verifies transcription before background launch (up to 3 attempts)
   - Displays previous runtime state from `%LOCALAPPDATA%\VoiceDictation\state.json`
   A colored circle then appears in your system tray.
   
   Optional launch modes:
   - `start-dictation.bat --healthcheck-only` (run checks and exit)
   - `start-dictation.bat --skip-healthcheck` (launch immediately)

2. **Dictate:**
   - Hold your hotkey (default: Alt+F)
   - Icon turns red and an ascending tone plays - speak clearly
   - Release the hotkey
   - A descending tone plays; icon turns yellow while processing
   - Text appears in your active window
   - Icon returns to green

   **Note:** Clipboard backup is disabled by default. Enable `USE_CLIPBOARD = True` in `src/config.py` if you want every transcript copied as backup.

3. **Open Mic Mode (hands-free):**
   - Right-click tray icon → **Enable Open Mic Mode**
   - Icon turns blue — listening for wake word
   - Say **"hey Jarvis"** (or your configured wake word)
   - Ascending tone plays — recording started
   - Speak naturally — the system detects when you stop talking
   - Descending tone plays — recording ended, transcribing
   - Text appears in your active window
   - Icon returns to blue, ready for the next wake word

   Both modes work simultaneously — you can use the hotkey anytime even with open mic enabled. Open mic mode uses [OpenWakeWord](https://github.com/dscripka/openWakeWord) for lightweight, local wake word detection on CPU.

   **First-time setup:** Install the dependency with `pip install openwakeword` (or re-run `install.bat`). Pre-trained wake words include "hey Jarvis", "alexa", "hey Mycroft", and others.

4. **Check status:** Hover over tray icon for current settings

5. **Run healthcheck while app is running:** Right-click tray icon → `Run Startup Healthcheck...`

6. **Exit:** Right-click tray icon → Exit

## Limitations

This tool is optimized for single-speaker dictation in reasonably quiet environments. Transcription accuracy may degrade in the following scenarios:

- **Background conversations** - Multiple voices speaking simultaneously
- **Noisy environments** - Loud ambient noise, machinery, or music
- **Distant microphone placement** - Speaking far from the microphone

For best results, use a close-range microphone and minimize background noise. If you experience issues with ambient noise, enable noise reduction in `src/config.py`:

```python
NOISE_REDUCTION = True
```

This applies audio filtering before transcription, which can help with stationary noise (fans, AC, traffic hum) but may not fully isolate your voice from other speakers.

### Text Injection Behavior

Transcribed text is injected into the active window using simulated keystrokes, paced in bursts: 32 characters at full speed, then a 100ms pause (configurable via `INJECT_CHUNK_CHARS` / `INJECT_CHUNK_PAUSE_S`). The pauses give terminal applications (notably Claude Code's TUI) time to drain their input queue without the latency of a flat per-character delay. Set `INJECT_CHUNK_CHARS = 0` to restore the legacy 10ms-per-character throttle.

If you want clipboard backup, enable it in `src/config.py`:

```python
USE_CLIPBOARD = True
```

## Uninstalling

Run `uninstall.bat` to remove:
- Virtual environment
- Configuration
- Log files
- Optionally: downloaded model cache (~500MB-3GB)

## Security Considerations

Hotkey detection uses the Win32 `RegisterHotKey` API (since v1.10.0). The OS delivers a notification only when the registered combination is pressed - the application never sees any other keystrokes on this path.

Two narrower uses of the `keyboard` library remain: text injection (`keyboard.write` sends synthetic keystrokes; it does not read input), and a fallback detection path used only when the hotkey cannot be registered natively (a bare modifier key like `right ctrl`, or a combination already owned by another application). The fallback installs a global keyboard hook that receives all keystrokes system-wide; it processes only the configured hotkey's press/release events and discards everything else immediately. No keystrokes are logged, stored, or transmitted on any path. Users should ensure the application is installed from a trusted source and review `src/voice_dictation/win_hotkey.py` and the `src/dictate.py` hotkey handlers if concerned.

## Troubleshooting

### "Python not found"
- Install Python (see Prerequisites above)
- Make sure "Add to PATH" was checked during installation
- Try restarting your computer

### "Access denied" or hotkey doesn't work
- If the tray icon is orange and an error tone played at startup, another application owns your hotkey combination - the app fell back to hook-based detection. Pick a different `HOTKEY` in `src/config.py`
- Text cannot be typed into elevated (admin) windows - this is a Windows security boundary, not a bug
- Right-click `start-dictation.bat` → "Run as administrator" only if you need dictation inside admin windows

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

After installation, edit `src/config.py` or re-run `install.bat`.
`install.bat` writes the core keys; `dictate.py` also supports additional optional keys below.
If an optional key is missing, runtime defaults are applied automatically.

```python
HOTKEY = 'alt+f'          # Your recording hotkey
MODEL_SIZE = 'small'      # tiny, base, small, medium, large, large-v3-turbo
BEAM_SIZE = 5             # Whisper decode beam: 1 = greedy (fastest), 5 = default
LANGUAGE = 'en'           # 'en', 'auto', 'es', 'fr', 'de', 'ja', etc.
DEVICE = 'cuda'           # 'cuda' or 'cpu'
COMPUTE_TYPE = 'float16'  # 'float16' for GPU, 'int8' for CPU
AUDIO_DEVICE = None       # Saved device name (auto-managed)
AUDIO_DEVICE_HOSTAPI = None  # Saved host API (auto-managed)
AUDIO_DEVICE_INDEX = None    # Saved preferred index (auto-managed)
AUDIO_DEVICE_UID = None      # Saved stable device fingerprint (auto-managed)
NOISE_REDUCTION = False   # True to filter background noise
NOISE_GATE_THRESHOLD = 0.01   # Base RMS gate threshold
NOISE_GATE_PEAK_MULTIPLIER = 3.0  # Allow low-RMS clips if peaks indicate speech
USE_CLIPBOARD = False     # Copy text to clipboard as backup (opt-in)
LOG_TRANSCRIPT_TEXT = False  # Log transcript snippets (debug only)
MAX_TYPED_CHARS = 1000    # Maximum characters typed per utterance
INJECT_CHUNK_CHARS = 32   # Chars typed per full-speed burst (0 = legacy 10ms/char)
INJECT_CHUNK_PAUSE_S = 0.1  # Pause between bursts
LOG_LEVEL = 'INFO'        # Runtime log verbosity (DEBUG/INFO/WARNING/ERROR)
VOCABULARY = ''           # Custom words: 'Claude, Anthropic, TypeScript'

# Open Mic / Wake Word Mode
WAKE_WORD_ENABLED = False          # Start with open mic on at launch
WAKE_WORD_MODEL = 'hey_jarvis_v0.1'  # Pre-trained model or path to custom .onnx
WAKE_WORD_THRESHOLD = 0.5         # Detection confidence (0.0-1.0)
WAKE_WORD_SILENCE_TIMEOUT_S = 2.0 # Seconds of silence to end a segment
WAKE_WORD_OUTPUT_FILE = None      # Path to transcription log file (optional)
```

### Custom Vocabulary

Whisper sometimes misinterprets names and technical terms. Add them to `VOCABULARY` in `config.py`:

```python
VOCABULARY = 'Claude, Anthropic, TypeScript, GitHub, JIRA'
```

This primes the model to recognize these spellings correctly. Just list the words separated by commas - the model learns the correct spelling from context. Restart the app after changing.

## Files

Detailed runtime and dependency architecture diagrams are documented in [`ARCHITECTURE.md`](ARCHITECTURE.md).

```text
voice-dictation/
|-- src/                # application modules
|   |-- voice_dictation/   # internal split modules (recording/watchdogs)
|-- tests/              # pytest suites
|-- install.bat
|-- start-dictation.bat
|-- test-install.bat
|-- launch.cmd
|-- uninstall.bat
|-- README.md
|-- ARCHITECTURE.md
|-- TESTING-PLAN.md
```

| File | Purpose |
|------|---------|
| `src/dictate.py` | Main orchestration + compatibility facade (tray/hotkey/runtime wiring) |
| `src/voice_dictation/win_hotkey.py` | Native Win32 RegisterHotKey detection (message-loop thread + release polling) |
| `src/voice_dictation/recording_pipeline.py` | Extracted recording/transcription preparation pipeline helpers |
| `src/voice_dictation/watchdog_loops.py` | Recording, stream-health, and keyboard-hook watchdog/recovery loops |
| `src/voice_dictation/wake_word_listener.py` | Wake word detection loop with energy-based silence timeout |
| `src/voice_dictation/shared_audio_buffer.py` | Thread-safe FIFO for audio frames between producer/consumer |
| `src/voice_dictation/wake_word_mode.py` | Wake word mode state management (enable/disable/toggle) |
| `src/voice_dictation/transcription_file_writer.py` | Plain text transcription log file writer |
| `src/diagnostics.py` | Diagnostic log and runtime-state analyzer |
| `src/startup_healthcheck.py` | Operational preflight + spoken phrase verification |
| `src/calibrate.py` | Noise gate calibration workflow |
| `src/audio_device_identity.py` | Shared microphone identity, UID, and fallback resolution helpers |
| `src/runtime_state.py` | Shared runtime state read/write helpers (`%LOCALAPPDATA%\VoiceDictation\state.json`) |
| `src/app_state.py` | Dataclass-backed runtime state container for dictation lifecycle |
| `src/audio_stream_manager.py` | Centralized stream open/close/switch/reopen behavior |
| `src/audio_capture.py` | Shared audio probe and fixed-duration capture helpers |
| `src/transcription_io.py` | Shared temporary WAV transcription pipeline for Whisper |
| `src/config_store.py` | Structured `config.py` read/update helpers with atomic writes |
| `src/speak.py` | Text-to-speech utility (see below) |
| `src/claude_status_tts.py` | Claude statusline helper (not part of dictation runtime) |
| `src/config.py` | Your settings (generated) |
| `src/config.example.py` | Configuration template |
| `install.bat` | Setup wizard (safe to re-run) |
| `uninstall.bat` | Remove installation |
| `start-dictation.bat` | Launch the tool |
| `launch.cmd` | Minimal headless launcher (starts `pythonw` directly, no startup healthcheck prompt) |
| `test-install.bat` | Verify installation |

### Tests

- `tests/test_dictate.py` - core tray/hotkey/transcription behavior
- `tests/test_dictate_runtime_guards.py` - runtime race/restart/guard regression coverage
- `tests/test_recording_pipeline.py` - transcription pipeline timing/beam-size behavior
- `tests/test_win_hotkey.py` - RegisterHotKey parsing and listener thread behavior
- `tests/test_hotkey_registration.py` - native-first registration, fallback, rehook, modifier cleanup gating
- `tests/test_keyboard_hook_watchdog.py` - dead-hook detection, polling rescue, proactive rehook
- `tests/test_stream_failure_alerts.py` - persistent audio failure alerting and recovery latch
- `tests/test_startup_healthcheck.py` - startup healthcheck behavioral flow
- `tests/test_diagnostics.py` - diagnostics parsing/aggregation/report output
- `tests/test_calibrate.py` - calibration and fallback behavior
- `tests/test_audio_device_identity.py` - shared device identity and resolution logic
- `tests/test_runtime_state.py` - shared runtime state persistence helpers
- `tests/test_config_store.py` - config assignment upsert and literal formatting
- `tests/test_audio_stream_manager.py` - stream lifecycle abstraction behavior
- `tests/test_audio_capture.py` - shared capture/probe helpers
- `tests/test_transcription_io.py` - temporary WAV transcription helper behavior
- `tests/test_app_state.py` - runtime state container defaults
- `tests/test_claude_status_tts.py` - command hardening for Claude statusline helper
- `tests/test_wake_word_listener.py` - wake word detection, silence timeout, frame exclusion
- `tests/test_wake_word_components.py` - file writer, shared buffer, mode toggle
- `tests/test_watchdog_recovery.py` - device re-resolve after persistent recovery failures

### speak.py - Text-to-Speech Utility

A standalone utility for text-to-speech using Microsoft Edge's neural voices:

```
.venv\Scripts\python src\speak.py "Hello, this is a test."
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
- **[openWakeWord](https://github.com/dscripka/openWakeWord)** - Wake word detection (Apache 2.0)
- **[edge-tts](https://github.com/rany2/edge-tts)** - Text-to-speech (MIT)
