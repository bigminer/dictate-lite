# Architecture Refactor Plan

Status: [x] Completed

## Objective
Reduce duplication and global coupling while preserving current behavior.

## Tasks
- [x] Create shared `src/audio_device_identity.py` for device enumeration, UID generation, resolution, and topology signatures.
- [x] Create shared `src/runtime_state.py` for runtime state pathing and atomic read/write operations.
- [x] Refactor `src/dictate.py` to consume shared modules.
- [x] Refactor `src/startup_healthcheck.py` and `src/calibrate.py` to consume shared modules.
- [x] Replace unbounded single-file logging with rotating log handler in `src/dictate.py`.

## Completion Criteria
- [x] No duplicate device UID logic remains across `dictate.py`, `startup_healthcheck.py`, and `calibrate.py`.
- [x] Runtime state reads/writes are centralized in one module.
- [x] Existing startup, tray, and healthcheck flows still run.
