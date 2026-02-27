# TODO

## Features

- [ ] **Open mic / wake word mode** — Hands-free dictation activated by a wake
  word (e.g., "dictate"). Runs as an optional mode alongside the default
  push-to-talk hotkey.

  ### Architecture
  ```
  Audio stream → OpenWakeWord (tiny, CPU, always-on)
                      ↓ wake phrase detected → trim wake word audio
                 Whisper (GPU, on-demand)
                      ↓
                 keyboard.write() + append to plain text log file
  ```

  ### Decided
  - **Wake word library**: OpenWakeWord (free, local, custom phrase training
    via synthetic TTS audio). No commercial dependency.
  - **Activation**: Wake word detector runs on CPU in ~5ms/frame. Triggers
    Whisper only when wake phrase is heard. Wake word audio is trimmed before
    passing to Whisper — the wake phrase itself is not included in the
    transcription.
  - **Deactivation**: Silence timeout (e.g., 2s) ends the segment. Optionally
    a stop word ("done", "stop") for explicit cutoff mid-pause.
  - **Mode toggle**: Tray menu toggle, off by default. Hotkey mode always
    available regardless. Tray icon turns **blue** when wake word listening
    is active (distinct from green/ready, red/recording, yellow/transcribing).
  - **Text output**: `keyboard.write()` injects text into the active window
    (same as hotkey mode). Clipboard is disabled in wake word mode. Also
    appends each transcription to a plain text log file (one line per segment).
  - **Config**: `WAKE_WORD`, `WAKE_WORD_ENABLED`, `WAKE_WORD_SILENCE_TIMEOUT_S`,
    `WAKE_WORD_OUTPUT_FILE` in `config.py`.

  ### Spike results
  - **Concurrent audio access**: RESOLVED. Tested 2025-02-27. Shared callback
    buffer works — one InputStream, callback fills a deque, both OpenWakeWord
    and Whisper read from it independently. Two simultaneous streams also
    work on this hardware but with lower throughput. **Recommendation: shared
    buffer approach** (simpler, no driver concerns).
