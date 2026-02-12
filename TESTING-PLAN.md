# Voice Dictation — Testing Plan

> Reliability fixes for zombie processes, device resilience, and microphone validation.
>
> **Date:** 2026-02-12

<br>

## How to Use This Plan

Start with the automated tests — they run in under a second and catch logic regressions
without any hardware. Then work through the manual sections in order.

Tests marked with **[A]** have automated coverage. If `pytest` passes, you can skip those
steps or just skim them as a sanity check.

```
.venv\Scripts\python -m pytest tests/ -v
```

> 61 tests | ~0.5 seconds | No microphone or GPU needed

<br>

## Before You Start

You'll need:

- A working microphone (built-in is fine)
- A USB mic or headset (for device-switching tests — skip those if unavailable)
- The app's venv set up (`install.bat`)
- pytest: `.venv\Scripts\pip install pytest`

Keep these open while testing:

- A console: `.venv\Scripts\python src\dictate.py`
- The log file: `%USERPROFILE%\voice-dictation\dictation.log`
- Explorer at: `%TEMP%\` (to watch for `voice-dictation.lock`)

<br>

---

<br>

## 1 — Smoke Test

*Does the app start and transcribe?*

<br>

- [ ] **1.1** &ensp; Launch from console. Tray icon goes gray then green.
  Log shows mic check, model load, stream started, and mic self-test.

- [ ] **1.2** &ensp; Verify the log contains
  `Microphone self-test passed (RMS=...)` with a non-zero value.

- [ ] **1.3** &ensp; Hold hotkey, speak a sentence, release.
  Tray: red, yellow, green. Text appears in the active window.

- [ ] **1.4** &ensp; Open `src/config.py`. `AUDIO_DEVICE` should be `None` or
  a quoted string like `'Microphone (USB Audio)'` — not an integer.

<br>

---

<br>

## 2 — Restart & Zombie Process Fixes

*Can you always restart the app — even after crashes, kills, and stale locks?*

<br>

### 2A — Normal Quit Cleans Up

- [ ] **2A.1** &ensp; Start the app. Confirm `%TEMP%\voice-dictation.lock`
  exists and contains the current PID.

- [ ] **2A.2** &ensp; Right-click tray, click **Quit**. Icon disappears.

- [ ] **2A.3** &ensp; Check the lock file — it should be **deleted**.
  *(This was the core bug: it was never cleaned up before.)*
  **[A]** `TestCleanupResources::test_removes_lock_file`

- [ ] **2A.4** &ensp; Start the app again immediately. It should launch
  without any "already running" block.

- [ ] **2A.5** &ensp; Quit again. Lock file deleted again.

<br>

### 2B — Stale Lock with Recycled PID

> **[A]** `TestIsPythonProcess` (3 tests) + `TestCheckSingleInstance::test_stale_lock_dead_pid`
> cover the PID validation logic. Skip to 2C if automated tests pass.

- [ ] **2B.1** &ensp; Make sure the app is NOT running.

- [ ] **2B.2** &ensp; In Task Manager, note the PID of any non-Python process
  (e.g., Chrome, Explorer).

- [ ] **2B.3** &ensp; Create a fake lock:
  `echo [that PID] > %TEMP%\voice-dictation.lock`

- [ ] **2B.4** &ensp; Start the app. It should start normally.
  Log: *"Process [PID] is not running or is not Python. Taking over lock."*

- [ ] **2B.5** &ensp; Quit cleanly. Lock file deleted.

<br>

### 2C — Stale Lock with Old Timestamp

> **[A]** `TestCheckSingleInstance::test_stale_lock_old_timestamp` covers
> the 24h threshold. Skip if automated tests pass.

- [ ] **2C.1** &ensp; Create a fake lock: `echo 99999 > %TEMP%\voice-dictation.lock`

- [ ] **2C.2** &ensp; Backdate it in PowerShell:
  ```powershell
  (Get-Item $env:TEMP\voice-dictation.lock).LastWriteTime = (Get-Date).AddHours(-25)
  ```

- [ ] **2C.3** &ensp; Start the app.
  Log: *"Lock file is X.X hours old - treating as stale"*

- [ ] **2C.4** &ensp; Quit cleanly. Lock deleted.

<br>

### 2D — Recovery After Task Manager Kill

- [ ] **2D.1** &ensp; Start the app normally. Green icon, lock file exists.

- [ ] **2D.2** &ensp; End the process via Task Manager.
  Lock file **remains** (expected — no cleanup on force kill).

- [ ] **2D.3** &ensp; Start the app again. It should detect the dead PID and
  take over the lock.

<br>

### 2E — Double-Launch Prevention

- [ ] **2E.1** &ensp; Start the app. Green icon.

- [ ] **2E.2** &ensp; Open a second console and try launching again.
  Second instance should exit silently.
  Log: *"Process [PID] is still running (confirmed Python). Exiting."*

- [ ] **2E.3** &ensp; Quit the first instance. Lock removed.

<br>

---

<br>

## 3 — Device Resilience Across Workstations

*Does the app gracefully handle different docking stations, USB mics, and hardware changes?*

<br>

### 3A — Device Saved by Name

> **[A]** `TestSaveAudioDeviceToConfig` (5 tests) + `TestResolveDeviceNameToIndex` (6 tests)

- [ ] **3A.1** &ensp; Right-click tray, **Select Microphone**, pick a specific device.
  Log: *"Switching audio device"* and *"New audio stream opened"*.

- [ ] **3A.2** &ensp; Open `src/config.py`. Verify `AUDIO_DEVICE` is a quoted
  string, not an integer. **[A]**

- [ ] **3A.3** &ensp; Quit and restart. Log: *"Using saved device by name"*.

<br>

### 3B — Legacy Integer Auto-Migration

> **[A]** `TestSaveAudioDeviceToConfig::test_overwrites_integer_format`

- [ ] **3B.1** &ensp; Edit `src/config.py`: set `AUDIO_DEVICE = 0`

- [ ] **3B.2** &ensp; Start the app.
  Log: *"AUDIO_DEVICE is an integer (0). Integer indices are deprecated."*
  then *"Migrated legacy index to name: '...'"*

- [ ] **3B.3** &ensp; Check `config.py` — auto-migrated to a quoted string. **[A]**

<br>

### 3C — Missing Device Fallback

> **[A]** `TestResolveDeviceNameToIndex::test_no_match_returns_none` +
> `TestCheckMicrophoneFallback::test_first_available_fallback`

- [ ] **3C.1** &ensp; Edit `config.py`: set
  `AUDIO_DEVICE = 'Nonexistent Fake Device XYZ'`

- [ ] **3C.2** &ensp; Start the app. Falls back to default.
  Log: *"Saved device name '...' not found, falling back to default"* **[A]**

- [ ] **3C.3** &ensp; Check `config.py` — updated to the actual fallback device name.

- [ ] **3C.4** &ensp; Quit and restart. Starts immediately on the persisted
  fallback (no repeated fallback loop).

<br>

### 3D — Safe Device Swap

> **[A]** `TestSwitchAudioDevice` (3 tests)

- [ ] **3D.1** &ensp; Start with a working mic. Green icon.

- [ ] **3D.2** &ensp; *(If second mic available)* Switch via tray menu.
  Log: new stream opened, old stream closed. **[A]**

- [ ] **3D.3** &ensp; Dictate on the new device. Transcription works.

- [ ] **3D.4** &ensp; *(If testable)* Unplug the active USB mic, then try
  selecting it in the menu. Log: *"Failed to open new stream"* and
  *"Keeping current audio device unchanged"*. Old stream still works. **[A]**

<br>

### 3E — Dynamic Tray Menu

- [ ] **3E.1** &ensp; Open **Select Microphone**. Note the device list.

- [ ] **3E.2** &ensp; Plug in a USB mic.

- [ ] **3E.3** &ensp; Open the menu again. New device appears **without restart**.

- [ ] **3E.4** &ensp; Unplug it. Open menu. Device is gone.

<br>

### 3F — Calibration Tool

> **[A]** `TestResolveAudioDevice` (7 tests) + `TestDivisionByZeroGuard` (5 tests)
> + `TestCalibrateSourcePatterns` (4 tests)

- [ ] **3F.1** &ensp; Run `.venv\Scripts\python src\calibrate.py`.
  Shows resolved device name and index. **[A]**

- [ ] **3F.2** &ensp; Complete calibration. If ambient was silent, shows
  *"N/A (ambient was silent)"* instead of crashing. **[A]**

- [ ] **3F.3** &ensp; Set `AUDIO_DEVICE = 'Nonexistent Device'` in config.

- [ ] **3F.4** &ensp; Run calibration again. Falls back with a warning. **[A]**

<br>

---

<br>

## 4 — Microphone Validation & Health

*Does the app detect bad mics, muted audio, and dead streams?*

<br>

### 4A — Startup Self-Test

- [ ] **4A.1** &ensp; Start with a working, unmuted mic.
  Log: *"Microphone self-test passed (RMS=X.XXXXXX)"*

- [ ] **4A.2** &ensp; Mute the mic at the OS level. Restart.
  Log: *"mic may be muted or disconnected"*. Tray tooltip shows warning.

- [ ] **4A.3** &ensp; Unmute. The warning is cosmetic — app still works.
  Dictating after unmuting produces transcription.

<br>

### 4B — Recording Timeout

> **[A]** `TestRecordingTimeout` (3 tests)

- [ ] **4B.1** &ensp; Temporarily set `MAX_RECORDING_SECONDS = 5` in `dictate.py`.

- [ ] **4B.2** &ensp; Hold the hotkey for >5 seconds.
  Recording force-stops. Log: *"Recording timeout after 5s"*. **[A]**

- [ ] **4B.3** &ensp; Do a normal dictation. Still works.

- [ ] **4B.4** &ensp; **Revert** `MAX_RECORDING_SECONDS = 120`.

<br>

### 4C — Silence Detection

> **[A]** `TestSilenceDetection` (3 tests) + `TestAudioCallback` (3 tests)

- [ ] **4C.1** &ensp; Mute the mic at the OS level.

- [ ] **4C.2** &ensp; Hold the hotkey. Tray tooltip changes to
  *"Recording - Warning: mic may be muted"*. **[A]**

- [ ] **4C.3** &ensp; Release. Noise gate triggers. Tray returns to green.

- [ ] **4C.4** &ensp; Unmute. Record normally. No warning. **[A]**

<br>

### 4D — Callback Logging

> **[A]** `TestAudioCallback::test_status_should_use_logger_not_print` —
> Skip 4D.1 if this passes.

- [ ] **4D.1** &ensp; Search the log for `"Audio status:"`. Should appear as
  `WARNING` log entries, not stderr prints. **[A]**

- [ ] **4D.2** &ensp; Launch via `start-dictation.bat` (pythonw mode).
  Audio warnings still appear in the **log file**.

<br>

### 4E — Stream Health Watchdog

*Requires a USB mic you can unplug while the app runs.*

- [ ] **4E.1** &ensp; Select a USB mic via tray. Green icon.

- [ ] **4E.2** &ensp; Unplug the USB mic. Within ~5-10 seconds:
  Log: *"Audio stream appears dead"*. Tray goes gray.

- [ ] **4E.3** &ensp; Wait a few more seconds. Watchdog attempts recovery.
  Log: *"Audio stream recovered successfully"*. Tray goes green.

- [ ] **4E.4** &ensp; Try dictating. Works on the fallback device.

- [ ] **4E.5** &ensp; Plug the USB mic back in. Select via tray. Dictation works.

<br>

### 4F — Short / Corrupt Audio

> **[A]** `TestStopRecordingCorruptionGuard` (5 tests) — Skip if passing.

- [ ] **4F.1** &ensp; Tap the hotkey very quickly (<100ms press-release).
  Log: *"Audio too short ... skipping transcription"*. No crash.
  Tray returns to green. **[A]**

<br>

---

<br>

## 5 — Integration

*Do all three fix groups play well together?*

<br>

### 5A — String Device Names End-to-End

> **[A]** `TestAudioDeviceStringCompatibility` (2 tests)

- [ ] **5A.1** &ensp; Select a mic via tray. Config has a quoted string. **[A]**

- [ ] **5A.2** &ensp; Restart. Mic self-test works with the string name.

- [ ] **5A.3** &ensp; Unplug to trigger watchdog. Recovery uses the string name.

<br>

### 5B — Docking Station Simulation

*The end-to-end scenario that matches your real-world workflow.*

- [ ] **5B.1** &ensp; "Workstation A" — Select a specific mic, dictate. Works.
  Config saved with device name.

- [ ] **5B.2** &ensp; Quit cleanly. Lock file removed.

- [ ] **5B.3** &ensp; Unplug the external mic ("move to workstation B").

- [ ] **5B.4** &ensp; Start the app. Falls back to default.
  Config updated. Mic self-test runs. Green tray.

- [ ] **5B.5** &ensp; Dictate. Works on the fallback device.

- [ ] **5B.6** &ensp; Plug the mic back in ("return to workstation A").
  Device appears in tray menu.

- [ ] **5B.7** &ensp; Select it via menu. Stream switches. Config updated.
  Dictation works.

<br>

### 5C — Crash Recovery During Recording

- [ ] **5C.1** &ensp; Start recording (hold hotkey). Tray is red.

- [ ] **5C.2** &ensp; Kill via Task Manager while recording. Lock file remains.

- [ ] **5C.3** &ensp; Restart the app. Stale lock detected. Starts normally.

<br>

---

<br>

## 6 — Edge Cases

*Lower priority. Note results for future reference.*

<br>

- [ ] **6.1** &ensp; String device names work with `sounddevice`
  *(Confirmed by 5A tests and automated suite)* **[A]**

- [ ] **6.2** &ensp; Watchdog doesn't false-trigger at startup
  *(Watch the log — no "stream appears dead" in the first 10 seconds)*

- [ ] **6.3** &ensp; `pystray.Menu()` accepts callable submenus
  *(Confirmed by 3E — menu rebuilds on each open)*

- [ ] **6.4** &ensp; SIGBREAK fires on console window close
  *(Start via `launch.cmd`, close with X button, check if lock was cleaned)*

- [ ] **6.5** &ensp; Bluetooth/virtual device sample rate
  *(If available, select a Bluetooth headset and watch log for PortAudio errors)*

- [ ] **6.6** &ensp; `pythonw.exe` mode works end-to-end
  *(Run via `start-dictation.bat` — tray appears, dictation works, quit cleans up)*

<br>

---

<br>

## Automated Test Reference

```
.venv\Scripts\python -m pytest tests/ -v
```

**61 tests across 15 test classes:**

| Group | File | Test Class | Count |
|:-----:|------|------------|:-----:|
| A | `test_dictate.py` | `TestIsPythonProcess` | 3 |
| A | `test_dictate.py` | `TestCheckSingleInstance` | 3 |
| A | `test_dictate.py` | `TestCleanupResources` | 3 |
| B | `test_dictate.py` | `TestResolveDeviceNameToIndex` | 6 |
| B | `test_dictate.py` | `TestSaveAudioDeviceToConfig` | 5 |
| B | `test_dictate.py` | `TestCheckMicrophoneFallback` | 6 |
| B | `test_dictate.py` | `TestSwitchAudioDevice` | 3 |
| B | `test_calibrate.py` | `TestResolveAudioDevice` | 7 |
| B | `test_calibrate.py` | `TestDivisionByZeroGuard` | 5 |
| B | `test_calibrate.py` | `TestCalibrateSourcePatterns` | 4 |
| C | `test_dictate.py` | `TestAudioCallback` | 3 |
| C | `test_dictate.py` | `TestRecordingTimeout` | 3 |
| C | `test_dictate.py` | `TestStopRecordingCorruptionGuard` | 5 |
| C | `test_dictate.py` | `TestSilenceDetection` | 3 |
| -- | `test_dictate.py` | `TestAudioDeviceStringCompatibility` | 2 |

<br>

**Manual-only** (requires hardware or GUI):

- Tray icon visual states
- Real mic recording and transcription
- Device plug/unplug detection
- Stream watchdog with real stream death
- Full docking station simulation
- `pythonw.exe` launch mode
- SIGBREAK on console close
- Double-launch prevention

<br>

---

<br>

## Test Environment

| Field | Value |
|-------|-------|
| OS Version | |
| Python Version | |
| sounddevice Version | |
| pystray Version | |
| Primary Mic | |
| Secondary Mic | |
| GPU / CUDA | |
| Whisper Model | |
