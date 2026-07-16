"""Tests for keyboard hook watchdog dead-hook detection and on_hook_suspect alerts."""

import logging
import threading
import time
from types import SimpleNamespace

import voice_dictation.watchdog_loops as watchdog_loops
from voice_dictation.watchdog_loops import (
    _hotkey_parts_to_vk_codes,
    run_keyboard_hook_watchdog,
)


def test_right_ctrl_backslash_combo_maps_to_vk_codes():
    assert _hotkey_parts_to_vk_codes(['right ctrl', '\\']) == [0xA3, 0xDC]


def _make_state():
    return SimpleNamespace(is_recording=False, is_processing=False, hotkey_rehook_count=0)


def _run_watchdog_briefly(monkeypatch, *, keys_pressed_fn, run_s=0.4, **overrides):
    """Run the watchdog in a thread with fast intervals and a fake key-state source."""
    state = overrides.pop('state', _make_state())
    shutdown = threading.Event()
    calls = SimpleNamespace(rehook=[], start=[], suspect=[])

    monkeypatch.setattr(watchdog_loops, '_are_all_vk_pressed', lambda vk_codes: keys_pressed_fn())

    kwargs = dict(
        state=state,
        shutdown_event=shutdown,
        hotkey_parts=['alt', 'f'],
        rehook_fn=lambda: calls.rehook.append(time.time()),
        start_recording_fn=lambda: calls.start.append(time.time()),
        on_hook_suspect=lambda reason: calls.suspect.append(reason),
        logger=logging.getLogger('test_keyboard_hook_watchdog'),
        poll_interval_s=0.01,
        recording_start_threshold_s=0.03,
        detection_threshold_s=0.1,
        proactive_rehook_interval_s=9999,
    )
    kwargs.update(overrides)

    thread = threading.Thread(target=run_keyboard_hook_watchdog, kwargs=kwargs, daemon=True)
    thread.start()
    time.sleep(run_s)
    shutdown.set()
    thread.join(timeout=2)
    return calls


def test_dead_hook_starts_recording_via_polling_and_alerts(monkeypatch):
    calls = _run_watchdog_briefly(monkeypatch, keys_pressed_fn=lambda: True)
    assert calls.start, 'expected polling bypass to start recording'
    assert any('recording started via polling' in r for r in calls.suspect)


def test_dead_hook_rehooks_after_detection_threshold_and_alerts(monkeypatch):
    calls = _run_watchdog_briefly(monkeypatch, keys_pressed_fn=lambda: True)
    assert calls.rehook, 'expected reactive rehook after detection threshold'
    assert any('hooks re-registered' in r for r in calls.suspect)


def test_idle_keys_trigger_nothing(monkeypatch):
    calls = _run_watchdog_briefly(monkeypatch, keys_pressed_fn=lambda: False, run_s=0.2)
    assert not calls.start
    assert not calls.rehook
    assert not calls.suspect


def test_recording_in_progress_suppresses_detection(monkeypatch):
    state = _make_state()
    state.is_recording = True
    calls = _run_watchdog_briefly(
        monkeypatch, keys_pressed_fn=lambda: True, run_s=0.2, state=state
    )
    assert not calls.start
    assert not calls.suspect


def test_suspect_callback_failure_does_not_kill_watchdog(monkeypatch):
    def _boom(reason):
        raise RuntimeError('alert plumbing broke')

    calls = _run_watchdog_briefly(
        monkeypatch,
        keys_pressed_fn=lambda: True,
        on_hook_suspect=_boom,
    )
    # Watchdog must survive the callback failure and still rehook.
    assert calls.rehook
