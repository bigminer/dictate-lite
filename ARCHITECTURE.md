# Voice Dictation Architecture (Agent-Oriented, ASCII)

This document is optimized for LLM/code-agent understanding and safe change planning.
It is ASCII-first on purpose.

## 1) Runtime Topology (System-Level)

```text
User
  |
  v
start-dictation.bat
  |
  +--> startup_healthcheck.py --(probe/capture/transcribe check)--> sounddevice + faster-whisper
  |         |
  |         +--> reads src/config.py
  |         +--> reads %LOCALAPPDATA%\VoiceDictation\state.json
  |
  +--> (if allowed) pythonw src/dictate.py
            |
            +--> pystray (tray icon/menu)
            +--> keyboard (global hotkey + text injection)
            +--> sounddevice InputStream (live mic callback)
            +--> faster-whisper (transcription)
            +--> src/config.py (device identity persistence)
            +--> %LOCALAPPDATA%\VoiceDictation\state.json (runtime lifecycle)
            +--> %USERPROFILE%\voice-dictation\dictation.log (rotating logs)
```

## 2) Source Layout and Ownership

```text
src/dictate.py               = Main orchestrator; tray/hotkey/state/stream/model lifecycle.
src/startup_healthcheck.py   = Pre-launch operational check and phrase verification.
src/calibrate.py             = Noise gate calibration; updates NOISE_GATE_THRESHOLD.

src/audio_device_identity.py = Source of truth for mic UID + resolution fallback chain.
src/audio_stream_manager.py  = Stream open/close/switch/reopen abstraction.
src/app_state.py             = DictationAppState dataclass + lock + shutdown event.
src/audio_capture.py         = Probe/capture helpers (deterministic stream reads).
src/transcription_io.py      = Whisper load + temp WAV transcription + text sanitize.
src/runtime_state.py         = Atomic read/write for %LOCALAPPDATA%\VoiceDictation\state.json.
src/config_store.py          = Atomic read/update of src/config.py assignments.
```

## 3) Import-Level Dependency Map

```text
dictate.py
  -> audio_device_identity
  -> audio_capture
  -> transcription_io
  -> runtime_state
  -> config_store
  -> audio_stream_manager
  -> app_state

startup_healthcheck.py
  -> audio_device_identity
  -> audio_capture
  -> transcription_io
  -> runtime_state

calibrate.py
  -> audio_device_identity
  -> config_store
```

## 4) Main Runtime Flow (Dictation)

```text
main()
  -> check_single_instance()
  -> write runtime_state = starting
  -> create tray icon (gray) [if pystray available]
  -> background init_audio_and_dictation()

init_audio_and_dictation()
  -> check_microphone() [device resolve fallback chain]
  -> load_model() [Whisper]
  -> open audio stream via AudioStreamManager
  -> set tray ready (green), write runtime_state = ready
  -> start stream_health_watchdog thread
  -> run_dictation_loop() [register hotkeys + wait]
```

### Hotkey Dictation Pipeline

```text
hotkey press
  -> start_recording()
     - guard: not already recording
     - guard: not currently processing
     - set STATE.is_recording = True
     - tray = red

audio_callback()
  -> if recording:
       append frames
       update silence flag

hotkey release (or watchdog fallback)
  -> stop_recording_and_transcribe()
     - transition recording -> processing
     - tray = yellow
     - _prepare_audio_for_transcription()
         * concat frames
         * min duration gate
         * noise gate threshold
         * optional noisereduce
     - _transcribe_and_emit_text()
         * transcription_io.transcribe_audio_array()
         * transcription_io.sanitize_transcript_text()
         * optional clipboard copy
         * keyboard.write() into active app
     - finish_processing_cycle()
         * STATE.is_processing = False
         * tray = green
```

## 5) Device Resolution and Recovery Logic

### Resolution Order (single source of truth in `audio_device_identity.py`)

```text
resolve_preferred_input_device(...)
  1. saved UID
  2. saved name (+ preferred host API/index tie-break)
  3. legacy saved numeric index
  4. system default input index
  5. first available input device
  6. none (failure)
```

### Recovery Loop

```text
stream_health_watchdog (every 5s, unless pipeline busy)
  -> detect device topology signature changes
  -> if changed:
       re-resolve preferred mic
       reopen stream if needed
  -> if stream missing/inactive/stale callbacks:
       _reopen_audio_stream(reason)
  -> on failure:
       tray gray + runtime_state audio_error
```

## 6) State Model (Operational)

### In-Memory Process State (`DictationAppState`)

```text
audio/recording fields:
  active_mic_name, active_mic_index, active_mic_hostapi
  audio_stream
  is_recording
  is_processing
  recorded_frames
  recording_start_time
  last_callback_time
  silence_flag

service/control fields:
  model
  tray_icon, tray_color, tray_title
  shutdown_event
  lock (RLock, coarse-grained)
```

### Persisted Runtime State (`%LOCALAPPDATA%\VoiceDictation\state.json`)

```text
write_runtime_state(status, reason?, details?, pid?)
  keys:
    status
    updated_at (UTC ISO-8601 Z)
    pid
    reason (optional)
    details (optional)
```

### Lifecycle State Machine (simplified)

```text
starting -> ready
starting -> audio_error
ready -> recording
recording -> processing
processing -> ready
ready -> audio_error
audio_error -> ready           (successful recovery/reopen)
any -> shutdown_clean          (cleanup_resources)
```

## 7) Side Effects Map (Critical for Safe Edits)

```text
Writes src/config.py:
  - dictate.py::save_audio_device_to_config()
  - calibrate.py::update_config()
  - via config_store.update_config_values()

Writes runtime state JSON:
  - dictate.py::_write_runtime_state()
  - startup_healthcheck.py reads only
  - via runtime_state.write_runtime_state()

Writes logs:
  - dictate.py logging setup (RotatingFileHandler)

Emits user-visible output:
  - tray icon/title updates (dictate.py)
  - keyboard text injection to active window (dictate.py)
  - optional clipboard write (dictate.py)
```

## 8) Concurrency and Guardrails

```text
STATE.lock:
  - protects recording/processing flags and frame list transitions.

_switch_lock:
  - serializes stream switch/reopen operations to avoid concurrent stream mutation.

background threads:
  - recording watchdog: release fallback + timeout + silence warning.
  - stream health watchdog: topology and stream liveness recovery.

rule:
  - do not switch/reopen stream while recording or processing.
```

## 9) Compatibility Constraints for Refactors

```text
dictate.py contains wrapper helpers delegating to audio_device_identity.
These wrappers exist to preserve existing call sites/tests.

When changing device identity behavior:
  - keep behavior aligned across dictate.py, startup_healthcheck.py, calibrate.py
  - update tests:
      tests/test_audio_device_identity.py
      tests/test_dictate.py
      tests/test_startup_healthcheck.py
      tests/test_calibrate.py
```

## 10) Test Coverage Map (Architecture-Relevant)

```text
tests/test_dictate.py                  = tray/hotkey/transcription runtime behavior
tests/test_dictate_runtime_guards.py   = runtime guard and safety behavior
tests/test_audio_device_identity.py    = UID + resolution chain
tests/test_audio_stream_manager.py     = stream lifecycle abstraction
tests/test_audio_capture.py            = probe/capture helpers
tests/test_transcription_io.py         = model I/O + text sanitize
tests/test_runtime_state.py            = state persistence
tests/test_config_store.py             = config upsert/atomic write
tests/test_startup_healthcheck.py      = startup verification flow
tests/test_calibrate.py                = calibration behavior
tests/test_app_state.py                = state defaults/container integrity
```

## 11) Fast Navigation for Agents

```text
Need to change typing/output behavior:
  -> dictate.py::_transcribe_and_emit_text()

Need to change stream open/switch/recover behavior:
  -> audio_stream_manager.py
  -> dictate.py::switch_audio_device()
  -> dictate.py::_reopen_audio_stream()
  -> dictate.py::stream_health_watchdog()

Need to change device matching/fallback:
  -> audio_device_identity.py (single source of truth)

Need to change persistence behavior:
  -> runtime_state.py / config_store.py
```
