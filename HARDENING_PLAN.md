# Voice Dictation Hardening Plan

This file tracks iterative robustness work. Each step has a Definition of Done and explicit validation checks.

## Step 1: Persistent Device Identity Upgrade

Status: [ ] Not started  [ ] In progress  [x] Complete

Goal:
- Persist a stronger microphone identity so dock/undock and device reordering do not break startup.

Tasks:
- [x] Add a durable `AUDIO_DEVICE_UID` field (auto-managed) to config templates/install output.
- [x] Compute and persist UID from selected input device metadata.
- [x] Resolve devices using UID first, then host API/index/name fallback.
- [x] Keep backward compatibility with existing `AUDIO_DEVICE*` fields.

Definition of Done:
- App can relaunch after simulated device reorder and still pick intended mic when UID matches.

Validation:
- [x] `.\.venv\Scripts\python.exe -m pytest tests -q`
- [ ] Manual: switch mic from tray, restart app, verify same mic remains selected.
- [ ] Manual: temporarily alter `AUDIO_DEVICE_INDEX`, verify UID-based resolution still works.

## Step 2: Hotplug Auto-Rebind

Status: [ ] Not started  [ ] In progress  [x] Complete

Goal:
- Automatically recover from dock/undock and active-device removal without manual restart.

Tasks:
- [x] Add a background device-topology watcher.
- [x] On input-device set change, re-resolve preferred mic identity.
- [x] If active stream device is gone or unhealthy, atomically reopen on best fallback.
- [x] Surface user-visible tray status while recovering.

Definition of Done:
- Removing/adding microphones no longer leaves the app stuck in audio-error state.

Validation:
- [x] `.\.venv\Scripts\python.exe -m pytest tests -q`
- [ ] Manual: unplug/disable active mic, verify auto-recovery within watchdog interval.
- [ ] Manual: dock/undock transition while app is running, verify stream resumes.

## Step 3: Crash-Safe Runtime Health State

Status: [ ] Not started  [ ] In progress  [x] Complete

Goal:
- Track startup/runtime health so crashes and failed launches provide deterministic recovery guidance.

Tasks:
- [x] Add atomic health-state file under `%LOCALAPPDATA%\VoiceDictation\state.json`.
- [x] Write lifecycle markers (`starting`, `ready`, `audio_error`, `shutdown_clean`).
- [x] Show actionable diagnostics in startup healthcheck and launch script when prior run was unhealthy.
- [x] Ensure idempotent behavior if state file is missing/corrupt.

Definition of Done:
- Startup surfaces last failure reason and does not require manual cleanup after crashes.

Validation:
- [x] `.\.venv\Scripts\python.exe -m pytest tests -q`
- [ ] Manual: force-close app, relaunch, verify stale state is detected and recovered.
- [ ] Manual: corrupt state file, relaunch, verify safe fallback.

## Step 4: Startup Control Flags

Status: [ ] Not started  [ ] In progress  [x] Complete

Goal:
- Support predictable startup flows for interactive use and automation.

Tasks:
- [x] Add `--healthcheck-only` mode for preflight validation.
- [x] Add `--skip-healthcheck` mode for trusted fast launch.
- [x] Document both paths in README and startup script prompts.
- [x] Keep default behavior as interactive healthcheck + phrase verification.

Definition of Done:
- Operators can choose strict validation vs fast startup intentionally.

Validation:
- [x] `.\.venv\Scripts\python.exe src\startup_healthcheck.py --skip-healthcheck`
- [ ] `.\.venv\Scripts\python.exe src\startup_healthcheck.py --healthcheck-only` (interactive mic phrase test)
- [ ] `start-dictation.bat` default path still runs phrase check.
- [x] README reflects both options.

## Final Verification Gate

- [x] `python -m py_compile src/dictate.py src/calibrate.py src/startup_healthcheck.py src/speak.py`
- [x] `.\.venv\Scripts\python.exe -m pytest tests -q`
- [ ] Manual smoke: launch -> healthcheck phrase pass -> tray ready -> dictation works.

