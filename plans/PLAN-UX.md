# UX Improvement Plan

Status: [x] Completed

## Objective
Improve startup usability and reduce operator confusion during failures.

## Tasks
- [x] Add retry flow to phrase verification in `src/startup_healthcheck.py`.
- [x] Add a tray action to launch healthcheck from the running app.
- [x] Update `README.md` with new UX behavior and launch/recovery guidance.

## Completion Criteria
- [x] Healthcheck can retry phrase capture at least once without relaunching script.
- [x] Users can trigger healthcheck from tray menu.
- [x] README documents retry and healthcheck-from-tray behavior.
