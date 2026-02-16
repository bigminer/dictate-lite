# Methodical Refactor Plan

Status: [x] Completed

## Objective
Implement the next architecture pass to improve maintainability, hardware resilience, and runtime behavior without breaking current workflows.

## Phase 1 - Baseline and Planning
- [x] Capture current scope and constraints from existing tests and scripts.
- [x] Define phased refactor tasks with verification criteria.

## Phase 2 - Shared Modules and State Model
- [x] Add `src/app_state.py` and migrate runtime mutable state into a dataclass-backed container.
- [x] Add `src/config_store.py` for structured config read/update with atomic writes.
- [x] Add `src/audio_capture.py` for shared record/probe helpers.
- [x] Add `src/transcription_io.py` for temporary WAV transcription flow.
- [x] Add `src/audio_stream_manager.py` to centralize stream open/close/recover/swap.

## Phase 3 - Integrate Refactors into Runtime
- [x] Refactor `src/dictate.py` to use `AppState`, `AudioStreamManager`, and shared helpers.
- [x] Replace release polling loop with event-driven hotkey release handling plus lightweight watchdog.
- [x] Refactor `src/startup_healthcheck.py` to use shared audio capture/transcription helpers.
- [x] Refactor `src/calibrate.py` to use shared config store and capture helpers where appropriate.

## Phase 4 - Validation and Documentation
- [x] Add/adjust unit tests for new modules and changed behavior.
- [x] Run static compile validation.
- [x] Run full `pytest` suite and record results.
- [x] Update this plan file with completed status and evidence.

## Verification Evidence
- [x] `python -m py_compile src/dictate.py src/startup_healthcheck.py src/calibrate.py src/app_state.py src/config_store.py src/audio_capture.py src/transcription_io.py src/audio_stream_manager.py` (pass)
- [x] `.venv\Scripts\python.exe -m pytest tests -q` (pass, `80 passed in 0.69s`)

## Completion Criteria
- [x] Existing tray/hotkey/device-switch/recovery flows still function.
- [x] Refactor modules are wired into runtime paths (not dead code).
- [x] Automated tests pass locally.
- [x] Plan is fully checked and marked completed.
