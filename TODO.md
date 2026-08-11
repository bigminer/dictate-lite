# TODO

## Features

- [x] **Open mic / wake word mode** — SHIPPED. Hands-free dictation activated by
  a wake word. Runs as an optional mode alongside the default push-to-talk
  hotkey (tray toggle, blue icon, silence-timeout segmentation). Notes below
  kept for historical context.

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

- [ ] **Confirmation mode (Open Mic + Confirm)** — After transcription, speak
  the text back to the user via Edge TTS (`speak.py`) instead of immediately
  typing. User says "send it" to confirm and inject, or "try again" to discard
  and re-record. Optional LLM cleanup step between transcription and readback
  to fix grammar, remove filler words, and tighten sentence structure.
  - **Phase 1**: TTS readback only (no LLM). Validates the UX flow.
  - **Phase 2**: Add LLM cleanup (Claude API or local model via Ollama).
  - **Phase 3**: Mute wake word listener during TTS playback to prevent
    feedback loop (mic picking up speaker output).
  - **Open questions**: Latency budget (~5-8s with LLM), audio feedback loop
    with non-bone-conduction mics, third tray mode toggle UX.

- [ ] **Custom wake word training** — Train a custom "dictate" wake phrase
  using OpenWakeWord's synthetic TTS training pipeline. Currently using
  pre-trained "hey Jarvis" as stand-in.
