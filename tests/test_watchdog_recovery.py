"""Tests for stream health watchdog device re-resolution after persistent failures."""

import logging
import threading
import time

from app_state import DictationAppState
from voice_dictation.watchdog_loops import run_stream_health_watchdog


# ---------------------------------------------------------------------------
# Lightweight fakes (following project pattern from test_audio_stream_manager)
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, active=True):
        self.active = active

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False


class _FakeManager:
    """Minimal stand-in for AudioStreamManager."""

    def __init__(self, stream=None):
        self.stream = stream

    @property
    def is_active(self):
        return self.stream is not None and self.stream.active

    def close(self):
        self.stream = None


# Stable topology that never triggers a topology-change branch.
_STABLE_DEVICES = [(1, {'name': 'Built-in Mic', 'max_input_channels': 1})]
_STABLE_SIG = ('stable',)


def _run_watchdog(*, state, shutdown, manager, reopen_fn, check_mic_fn,
                  update_tray_fn=None, write_state_fn=None, **overrides):
    """Convenience wrapper that fills in boring defaults."""
    tray_calls = [] if update_tray_fn is None else None
    state_calls = [] if write_state_fn is None else None

    def _tray(color, title=''):
        if tray_calls is not None:
            tray_calls.append((color, title))
        if update_tray_fn is not None:
            update_tray_fn(color, title)

    def _wstate(*a, **kw):
        if state_calls is not None:
            state_calls.append((a, kw))
        if write_state_fn is not None:
            write_state_fn(*a, **kw)

    kwargs = dict(
        state=state,
        shutdown_event=shutdown,
        is_audio_pipeline_busy=lambda: False,
        enumerate_input_devices=lambda: _STABLE_DEVICES,
        current_input_topology_signature=lambda devs: _STABLE_SIG,
        check_microphone=check_mic_fn,
        reopen_audio_stream_fn=reopen_fn,
        update_tray_icon=_tray,
        write_runtime_state=_wstate,
        get_stream_manager=lambda: manager,
        logger=logging.getLogger('test_watchdog_recovery'),
        watchdog_poll_s=0.001,
        backoff_max_s=0.05,
    )
    kwargs.update(overrides)
    run_stream_health_watchdog(**kwargs)
    return tray_calls, state_calls


# ---------------------------------------------------------------------------
# Cycle 1: After N consecutive failures, check_microphone is called
# ---------------------------------------------------------------------------

def test_check_microphone_called_after_consecutive_failures():
    state = DictationAppState()
    state.active_mic_index = 5          # stale device
    state.last_device_topology_signature = _STABLE_SIG
    shutdown = threading.Event()
    manager = _FakeManager(stream=None)  # Branch B: stream missing

    reopen_count = 0
    check_mic_count = 0

    def reopen_fn(reason):
        nonlocal reopen_count
        reopen_count += 1
        return False  # always fail

    def check_mic_fn():
        nonlocal check_mic_count
        check_mic_count += 1
        return False

    # Let the watchdog run for enough time that 3 failures + backoff elapse,
    # then shut down.
    threading.Timer(0.3, shutdown.set).start()
    _run_watchdog(state=state, shutdown=shutdown, manager=manager,
                  reopen_fn=reopen_fn, check_mic_fn=check_mic_fn)

    assert reopen_count >= 3, f'Expected >=3 reopen attempts, got {reopen_count}'
    assert check_mic_count >= 1, (
        f'Expected check_microphone to be called after {reopen_count} failures, '
        f'but it was called {check_mic_count} times'
    )


# ---------------------------------------------------------------------------
# Cycle 2: Successful re-resolve resets the failure counter
# ---------------------------------------------------------------------------

def test_immediate_retry_after_device_re_resolved():
    """When check_microphone returns True, consecutive_failures must reset.

    Without the reset, check_microphone fires on EVERY subsequent failure
    (4th, 5th, 6th, ...) since consecutive_failures stays >= threshold.
    With the reset, it only fires again after another full round of failures.
    """
    state = DictationAppState()
    state.active_mic_index = 5
    state.last_device_topology_signature = _STABLE_SIG
    shutdown = threading.Event()
    manager = _FakeManager(stream=None)

    reopen_count = 0
    check_mic_count = 0

    def reopen_fn(reason):
        nonlocal reopen_count
        reopen_count += 1
        if reopen_count >= 6:
            shutdown.set()
        return False  # always fail

    def check_mic_fn():
        nonlocal check_mic_count
        check_mic_count += 1
        state.active_mic_index = 2
        return True

    _run_watchdog(state=state, shutdown=shutdown, manager=manager,
                  reopen_fn=reopen_fn, check_mic_fn=check_mic_fn)

    assert reopen_count >= 6, f'Expected >=6 reopen attempts, got {reopen_count}'
    # With reset: check_mic fires at failures 3 and 6 → exactly 2 calls.
    # Without reset: fires at failures 3, 4, 5, 6 → 4 calls.
    assert check_mic_count == 2, (
        f'Expected check_microphone called exactly 2 times (reset after each), '
        f'got {check_mic_count}'
    )


# ---------------------------------------------------------------------------
# Cycle 3: No device found → stop retrying, show gray tray
# ---------------------------------------------------------------------------

def test_stops_retrying_when_no_device_found():
    state = DictationAppState()
    state.active_mic_index = 5
    state.last_device_topology_signature = _STABLE_SIG
    shutdown = threading.Event()
    manager = _FakeManager(stream=None)

    reopen_count = 0
    check_mic_count = 0

    def reopen_fn(reason):
        nonlocal reopen_count
        reopen_count += 1
        return False

    def check_mic_fn():
        nonlocal check_mic_count
        check_mic_count += 1
        state.active_mic_index = None  # no device available
        return False

    # Run long enough that extra retries would happen if not stopped.
    threading.Timer(0.3, shutdown.set).start()
    tray_calls, _ = _run_watchdog(
        state=state, shutdown=shutdown, manager=manager,
        reopen_fn=reopen_fn, check_mic_fn=check_mic_fn,
    )

    # Should stop after 3 failures — no further reopen attempts.
    assert reopen_count == 3, (
        f'Expected exactly 3 reopen calls (no retries after re-resolve failed), '
        f'got {reopen_count}'
    )
    assert check_mic_count >= 1, 'check_microphone should be called at least once'
    # Tray should show gray "No microphone" state from the re-resolve path.
    gray_calls = [c for c in tray_calls if c[0] == 'gray' and 'No microphone' in c[1]]
    assert gray_calls, f'Expected gray "No microphone" tray update, got: {tray_calls}'


# ---------------------------------------------------------------------------
# Cycle 4: Re-resolve also triggers from Branch C (stream inactive)
# ---------------------------------------------------------------------------

def test_re_resolve_triggers_from_stale_callback_path():
    """Branch C: stream exists but is_active=False (dead stream)."""
    state = DictationAppState()
    state.active_mic_index = 5
    state.last_device_topology_signature = _STABLE_SIG
    state.last_callback_time = time.time()  # non-zero so stale check can fire
    shutdown = threading.Event()
    dead_stream = _FakeStream(active=False)
    manager = _FakeManager(stream=dead_stream)

    reopen_count = 0
    check_mic_count = 0

    def reopen_fn(reason):
        nonlocal reopen_count
        reopen_count += 1
        # Keep the stream as a dead fake so we stay in Branch C.
        manager.stream = _FakeStream(active=False)
        if reopen_count >= 6:
            shutdown.set()
        return False

    def check_mic_fn():
        nonlocal check_mic_count
        check_mic_count += 1
        state.active_mic_index = 2
        return True

    _run_watchdog(state=state, shutdown=shutdown, manager=manager,
                  reopen_fn=reopen_fn, check_mic_fn=check_mic_fn)

    assert reopen_count >= 6, f'Expected >=6 reopen attempts, got {reopen_count}'
    # Same as Cycle 2: with reset, check_mic fires at failures 3 and 6.
    assert check_mic_count == 2, (
        f'Expected check_microphone called 2 times via Branch C, got {check_mic_count}'
    )


# ---------------------------------------------------------------------------
# Cycle 5: Transient failures below threshold don't trigger re-resolve
# ---------------------------------------------------------------------------

def test_transient_failures_use_normal_backoff():
    """Regression guard: fewer than re_resolve_after_failures should NOT call check_microphone."""
    state = DictationAppState()
    state.active_mic_index = 5
    state.last_device_topology_signature = _STABLE_SIG
    shutdown = threading.Event()
    manager = _FakeManager(stream=None)

    reopen_count = 0
    check_mic_count = 0

    def reopen_fn(reason):
        nonlocal reopen_count
        reopen_count += 1
        if reopen_count <= 2:
            return False  # first 2 fail (below threshold of 3)
        shutdown.set()
        return True  # 3rd succeeds

    def check_mic_fn():
        nonlocal check_mic_count
        check_mic_count += 1
        return True

    _run_watchdog(state=state, shutdown=shutdown, manager=manager,
                  reopen_fn=reopen_fn, check_mic_fn=check_mic_fn)

    assert reopen_count == 3, f'Expected 3 reopen calls (2 fail + 1 succeed), got {reopen_count}'
    assert check_mic_count == 0, (
        f'check_microphone should NOT be called for transient failures, '
        f'but was called {check_mic_count} times'
    )
