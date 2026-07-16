"""Tests for persistent-failure alerts and recovery latch in the stream health watchdog."""

import logging
import threading
import time
from types import SimpleNamespace

from voice_dictation.watchdog_loops import run_stream_health_watchdog


class _FakeManager:
    def __init__(self):
        self.stream = None

    @property
    def is_active(self):
        return self.stream is not None and self.stream.active

    def close(self):
        self.stream = None


_STABLE_DEVICES = [(1, {'name': 'Built-in Mic', 'max_input_channels': 1})]


def _make_state():
    return SimpleNamespace(
        last_device_topology_signature=None,
        active_mic_index=1,
        audio_stream=None,
        last_callback_time=0.0,
        tray_icon=None,
    )


def _run(reopen_factory, *, run_s=0.6, alert_after=3):
    """Run the watchdog with a missing stream so it retries reopen continuously."""
    state = _make_state()
    shutdown = threading.Event()
    manager = _FakeManager()
    alerts = []
    recoveries = []

    kwargs = dict(
        state=state,
        shutdown_event=shutdown,
        is_audio_pipeline_busy=lambda: False,
        enumerate_input_devices=lambda: _STABLE_DEVICES,
        current_input_topology_signature=lambda devs: ('stable',),
        check_microphone=lambda: True,
        reopen_audio_stream_fn=reopen_factory(manager),
        update_tray_icon=lambda color, title='': None,
        write_runtime_state=lambda *a, **kw: None,
        get_stream_manager=lambda: manager,
        logger=logging.getLogger('test_stream_failure_alerts'),
        watchdog_poll_s=0.001,
        backoff_max_s=0.002,
        on_persistent_failure=lambda n: alerts.append(n),
        on_recovery=lambda: recoveries.append(time.time()),
        alert_after_failures=alert_after,
    )
    thread = threading.Thread(target=run_stream_health_watchdog, kwargs=kwargs, daemon=True)
    thread.start()
    time.sleep(run_s)
    shutdown.set()
    thread.join(timeout=2)
    return alerts, recoveries


def test_alert_fires_once_after_threshold():
    alerts, recoveries = _run(lambda manager: lambda reason: False)
    assert alerts, 'expected on_persistent_failure after repeated reopen failures'
    assert len(alerts) == 1, 'alert must be latched — fired once per failure streak'
    assert alerts[0] >= 3
    assert not recoveries


def test_recovery_after_alert_fires_on_recovery_once():
    calls = {'n': 0}

    def factory(manager):
        def reopen(reason):
            calls['n'] += 1
            if calls['n'] < 6:
                return False
            manager.stream = SimpleNamespace(active=True)
            return True
        return reopen

    alerts, recoveries = _run(factory)
    assert len(alerts) == 1
    assert len(recoveries) == 1, 'expected exactly one on_recovery after the alert'


def test_no_alert_below_threshold():
    calls = {'n': 0}

    def factory(manager):
        def reopen(reason):
            calls['n'] += 1
            if calls['n'] < 2:
                return False
            manager.stream = SimpleNamespace(active=True)
            return True
        return reopen

    alerts, recoveries = _run(factory)
    assert not alerts
    assert not recoveries, 'on_recovery must only fire after an alert was raised'
