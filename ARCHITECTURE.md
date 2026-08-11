# Voice Dictation Architecture (Agent-Oriented, ASCII)

This document is optimized for LLM/code-agent understanding and safe change planning.
All diagrams are ASCII.

## 1) Repository File Structure (Current)

```text
voice-dictation/
|-- src/
|   |-- dictate.py                 # Runtime orchestrator + compatibility facade
|   |-- voice_dictation/
|   |   |-- win_hotkey.py          # Native Win32 RegisterHotKey detection (parse + listener thread)
|   |   |-- recording_pipeline.py  # Extracted recording/transcription prep helpers
|   |   |-- watchdog_loops.py      # Recording + stream + keyboard-hook watchdog loops, VK map
|   |   |-- wake_word_listener.py  # Wake word detection loop + energy silence timeout
|   |   |-- shared_audio_buffer.py # Thread-safe int16 frame FIFO (callback -> listener)
|   |   |-- wake_word_mode.py      # Wake word mode enable/disable/toggle state
|   |   |-- transcription_file_writer.py # Optional plain-text transcription log
|   |-- startup_healthcheck.py     # Startup/on-demand operational check
|   |-- calibrate.py               # Noise gate calibration workflow
|   |-- diagnostics.py             # Log + state analyzer
|   |-- audio_device_identity.py   # Device identity/UID + fallback resolution
|   |-- audio_stream_manager.py    # Stream open/close/switch/reopen abstraction
|   |-- audio_capture.py           # Stream probe + fixed duration capture helpers
|   |-- transcription_io.py        # Model load/transcription/sanitize helpers
|   |-- runtime_state.py           # %LOCALAPPDATA% state.json read/write
|   |-- app_state.py               # In-memory state container (DictationAppState)
|   |-- config_store.py            # Structured config.py update helpers
|   |-- config.example.py          # Template defaults
|   |-- config.py                  # Generated machine-local config (runtime input)
|   |-- create_icon.py             # Icon helper
|   |-- speak.py                   # TTS helper
|   |-- claude_status_tts.py       # Claude statusline/TTS helper (separate from dictation runtime)
|
|-- tests/
|   |-- test_dictate.py
|   |-- test_dictate_runtime_guards.py
|   |-- test_recording_pipeline.py
|   |-- test_win_hotkey.py
|   |-- test_hotkey_registration.py
|   |-- test_keyboard_hook_watchdog.py
|   |-- test_stream_failure_alerts.py
|   |-- test_watchdog_recovery.py
|   |-- test_wake_word_listener.py
|   |-- test_wake_word_components.py
|   |-- test_startup_healthcheck.py
|   |-- test_calibrate.py
|   |-- test_diagnostics.py
|   |-- test_audio_device_identity.py
|   |-- test_audio_stream_manager.py
|   |-- test_audio_capture.py
|   |-- test_transcription_io.py
|   |-- test_runtime_state.py
|   |-- test_config_store.py
|   |-- test_app_state.py
|   |-- test_claude_status_tts.py
|   |-- conftest.py
|
|-- install.bat
|-- start-dictation.bat
|-- test-install.bat
|-- launch.cmd
|-- uninstall.bat
|-- README.md
|-- TESTING-PLAN.md
|-- AGENTS.md
|-- ARCHITECTURE.md
```

## 2) Runtime Topology (System-Level)

```text
User
  |
  v
start-dictation.bat
  |
  +--> python src/startup_healthcheck.py --healthcheck-only
  |       |
  |       +--> sounddevice InputStream probe/capture
  |       +--> faster-whisper transcription check
  |       +--> reads src/config.py
  |       +--> reads %LOCALAPPDATA%\VoiceDictation\state.json
  |
  +--> start pythonw src/dictate.py
          |
          +--> pystray (tray icon + menu)
          +--> Win32 RegisterHotKey via voice_dictation/win_hotkey.py (hotkey detection)
          +--> keyboard (text injection; hook-based detection only as fallback)
          +--> sounddevice InputStream (live callback)
          +--> openwakeword ONNX (optional open mic wake word, CPU)
          +--> faster-whisper (transcription)
          +--> reads/writes src/config.py (device identity)
          +--> writes %LOCALAPPDATA%\VoiceDictation\state.json (lifecycle/status)
          +--> writes %USERPROFILE%\voice-dictation\dictation.log (rotating logs)
```

## 3) Core Responsibility Map

```text
dictate.py:
  Process lifecycle, singleton guard, tray UI, hotkey loop,
  live stream callback, restart handoff, runtime state updates,
  and wrappers around extracted pipeline/watchdog modules.

voice_dictation/win_hotkey.py:
  Hotkey combo -> (MOD_* flags, VK) parsing and the RegisterHotKey
  listener thread: GetMessageW loop, WM_HOTKEY -> on_press, polled
  GetAsyncKeyState release detection -> on_release.

voice_dictation/recording_pipeline.py:
  Recording -> processing transition, audio validation/gating,
  and transcription/sanitization timing payload generation.

voice_dictation/watchdog_loops.py:
  Recording watchdog loop, microphone self-test, stream reopen helper,
  stream health watchdog loop with exponential backoff + failure alerts,
  keyboard hook watchdog (polling rescue, reactive/proactive re-register),
  and the VK code map shared with win_hotkey.

voice_dictation/wake_word_listener.py + shared_audio_buffer.py + wake_word_mode.py:
  Open mic pipeline: callback feeds int16 FIFO, listener runs OpenWakeWord
  predict, energy-based silence timeout ends segments; mode toggle state.

startup_healthcheck.py:
  Pre-launch mic + transcription verification and guided user check.

calibrate.py:
  Ambient/speech sampling and NOISE_GATE_THRESHOLD update.

diagnostics.py:
  Parse/aggregate logs + runtime state into incident-friendly report.

audio_device_identity.py:
  Canonical input device normalization, UID generation, and resolution chain.

audio_stream_manager.py:
  Safe stream lifecycle transitions for open/switch/reopen/close.

audio_capture.py:
  Deterministic stream probe/capture helpers using blocking reads.

transcription_io.py:
  Whisper load/transcribe wrapper and transcript sanitization.

runtime_state.py + app_state.py:
  Persistent state.json + in-memory runtime state container.
```

## 4) Import Dependency Map

```text
dictate.py
  -> audio_device_identity
  -> audio_capture
  -> transcription_io
  -> runtime_state
  -> config_store
  -> app_state
  -> audio_stream_manager
  -> voice_dictation.recording_pipeline
  -> voice_dictation.watchdog_loops
  -> voice_dictation.win_hotkey
  -> voice_dictation.shared_audio_buffer
  -> voice_dictation.wake_word_mode
  -> voice_dictation.wake_word_listener (lazy, at listener start)
  -> voice_dictation.transcription_file_writer (lazy, at first write)

voice_dictation/win_hotkey.py
  -> voice_dictation.watchdog_loops (VK map)

startup_healthcheck.py
  -> audio_device_identity
  -> audio_capture
  -> transcription_io
  -> runtime_state

calibrate.py
  -> audio_device_identity
  -> audio_capture
  -> config_store

diagnostics.py
  -> runtime_state
```

## 5) Main Runtime Flow (Dictation)

### Startup

```text
main()
  -> check_single_instance()
  -> write runtime_state = starting
  -> create tray icon (gray)
  -> init_audio_and_dictation() on background thread

init_audio_and_dictation()
  -> check_microphone() via audio_device_identity resolution chain
  -> load_model()
  -> open stream via AudioStreamManager.open()
  -> write runtime_state = ready
  -> start stream_health_watchdog thread
  -> _register_hotkey() + watchdog threads + main wait loop

_register_hotkey()
  -> win_hotkey.parse_hotkey(HOTKEY_PARTS)
     parses?  -> WinHotkeyListener.start() on dedicated thread
                 RegisterHotKey OK   -> native path active
                 RegisterHotKey FALSE-> error tone + orange degraded latch,
                                        fall through to keyboard-lib hooks
     bare modifier / unmappable -> keyboard-lib hooks
        single key -> keyboard.hook_key(suppress=True)
        combo      -> keyboard.add_hotkey press/release pair (suppress=True)
```

### Recording and Transcription Pipeline

```text
Hotkey press (WM_HOTKEY on win-hotkey thread, or fallback hook callback,
              or keyboard-hook watchdog polling rescue)
  -> start_recording()
     -> set STATE.is_recording = True
     -> tray red + ascending tone

audio_callback()
  -> append frames while recording
  -> update last_callback_time
  -> update silence_flag

Hotkey release (win-hotkey release poll: any combo key up via
                GetAsyncKeyState; or fallback hook release callback;
                or recording watchdog release fallback)
  -> stop_recording_and_transcribe()
      -> descending tone
      -> set processing state + tray yellow
      -> voice_dictation.recording_pipeline.prepare_audio_for_transcription()
         - concatenate frames
         - duration gate
         - noise gate: RMS + peak-aware check
         - optional noise reduction
      -> voice_dictation.recording_pipeline.transcribe_audio()
         - faster-whisper transcribe (beam width from BEAM_SIZE config)
         - sanitize text
         - log duration + realtime factor
      -> _transcribe_and_emit_text()
         - optional clipboard copy
         - keyboard.write() in bursts (INJECT_CHUNK_CHARS per burst,
           INJECT_CHUNK_PAUSE_S between; 0 = legacy flat 10ms/char)
      -> finish -> tray green
```

### Open Mic / Wake Word Flow (optional, tray toggle)

```text
audio_callback() (when mode enabled)
  -> SharedAudioBuffer.put(int16 frames)

run_wake_word_listener() thread
  -> OpenWakeWord predict per frame (CPU, ONNX)
  -> score >= threshold
     -> start-recording callback (ascending tone, tray red)
     -> frames accumulate; RMS energy tracks speech
  -> silence_timeout_s of low energy
     -> stop callback (descending tone)
     -> background thread runs the same Whisper pipeline as hotkey mode
  -> optional transcription_file_writer append per segment

Hotkey and wake word modes coexist; wake word activation is ignored
while a hotkey recording/processing cycle is in flight.
```

## 6) Restart Handoff Flow

```text
Tray menu -> Restart
  -> on_tray_restart()
     -> spawn new process: pythonw dictate.py --restart-after-pid <old_pid>
     -> cleanup_resources(status=restarting, reason=tray_restart_requested)
     -> old process exits

New process startup
  -> sees --restart-after-pid
  -> waits for old PID exit
  -> retries singleton mutex acquisition for bounded window
  -> proceeds through normal startup
```

## 7) Device Resolution and Recovery

### Resolution Order

```text
resolve_preferred_input_device(...)
  1) Saved UID
  2) Saved name + host API/index tie-breakers
  3) Legacy numeric index
  4) System default input index
  5) First available input device
  6) Failure
```

### Watchdog Recovery Loop

```text
stream_health_watchdog wrapper
  -> voice_dictation.watchdog_loops.run_stream_health_watchdog(...)
  -> detect input topology signature changes
  -> re-resolve preferred mic on topology change
  -> if stream missing/inactive/stale callbacks:
       voice_dictation.watchdog_loops.reopen_audio_stream(...)
  -> on repeated failures:
       exponential backoff + runtime_state audio_error
```

## 8) State Model

### In-Memory (`DictationAppState`)

```text
Audio pipeline:
  is_recording, is_processing, recorded_frames, recording_start_time,
  silence_flag, last_callback_time, audio_stream

Device identity:
  active_mic_name, active_mic_index, active_mic_hostapi,
  last_device_topology_signature

Service:
  model, tray_icon, tray_color, tray_title, shutdown_event, lock

Health/degradation:
  hook_degraded, audio_degraded, hotkey_rehook_count,
  last_hotkey_callback_time

Session metrics:
  session_id, utterance_count, total_recording_ms, total_chars_typed,
  device_fallback_count, transcription_errors
```

### Persistent (`%LOCALAPPDATA%\VoiceDictation\state.json`)

```text
{
  "status": "...",
  "updated_at": "...Z",
  "pid": 12345,
  "reason": "...",         # optional
  "details": { ... }       # optional
}
```

Lifecycle values in practice:

```text
starting -> ready
ready -> heartbeat
ready/heartbeat -> recording -> processing -> ready
ready/heartbeat -> audio_error
ready/heartbeat -> restarting
any -> shutdown_clean
```

## 9) Side Effects and Persistence Boundaries

```text
Writes src/config.py:
  - dictate.py::save_audio_device_to_config()
  - calibrate.py::update_config()
  - via config_store.update_config_values()

Writes runtime state:
  - dictate.py::_write_runtime_state()
  - runtime_state.write_runtime_state()

Writes logs:
  - dictate.py logging setup (RotatingFileHandler)
  - file: %USERPROFILE%\voice-dictation\dictation.log

External user-visible actions:
  - tray icon/title updates
  - keyboard text injection into active window
  - optional clipboard writes
```

## 10) Test Coverage Map (Architecture-Relevant)

```text
tests/test_dictate_runtime_guards.py   -> restart, race, processing guard, tone, injection pacing regressions
tests/test_dictate.py                  -> broad behavior + source-level guard checks
tests/test_recording_pipeline.py       -> transcription timing log + beam-size passthrough
tests/test_win_hotkey.py               -> RegisterHotKey parse + listener thread (fake Win32 API)
tests/test_hotkey_registration.py      -> native-first registration, fallback, rehook gating
tests/test_keyboard_hook_watchdog.py   -> polling rescue, reactive/proactive re-register
tests/test_stream_failure_alerts.py    -> persistent-failure alert latch + recovery callbacks
tests/test_watchdog_recovery.py        -> device re-resolve after repeated recovery failures
tests/test_wake_word_listener.py       -> wake word detect, silence timeout, frame routing
tests/test_wake_word_components.py     -> shared buffer, mode toggle, file writer
tests/test_diagnostics.py              -> log parsing/aggregation/reporting
tests/test_startup_healthcheck.py      -> preflight phrase flow
tests/test_calibrate.py                -> calibration and threshold logic checks
tests/test_audio_device_identity.py    -> identity + fallback resolution
tests/test_audio_stream_manager.py     -> stream lifecycle abstraction
tests/test_audio_capture.py            -> probe/capture helpers
tests/test_transcription_io.py         -> transcript sanitize/transcribe wrappers
tests/test_runtime_state.py            -> persistent state read/write semantics
tests/test_config_store.py             -> config assignment updates
tests/test_app_state.py                -> app state defaults
```

## 11) Fast Navigation for Agents

```text
Need to debug "hotkey doesn't fire" or "recording stops instantly":
  -> voice_dictation/win_hotkey.py (WinHotkeyListener, release poll)
  -> dictate.py::_register_hotkey() (native vs fallback path selection)
  -> "registered via Win32 RegisterHotKey" + watchdog lines in dictation.log
  -> instant stop = release poll saw a combo key up (tap instead of hold?)

Need to debug "red but no transcription":
  -> dictate.py::_prepare_audio_for_transcription()
  -> check noise-gate logs in dictation.log

Need to debug restart/handoff:
  -> dictate.py::on_tray_restart()
  -> dictate.py::check_single_instance()
  -> state.json reason/status transitions

Need to debug device switching:
  -> audio_device_identity.py
  -> audio_stream_manager.py
  -> dictate.py::stream_health_watchdog()

Need to debug startup preflight:
  -> startup_healthcheck.py
```
