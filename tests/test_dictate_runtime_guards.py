"""Behavioral runtime guard tests for src/dictate.py."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np


SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')


def _import_dictate_module(config_overrides=None):
    config_values = {
        'HOTKEY': 'alt+f',
        'MODEL_SIZE': 'small',
        'DEVICE': 'cpu',
        'COMPUTE_TYPE': 'int8',
        'AUDIO_DEVICE': None,
        'LANGUAGE': 'en',
        'VOCABULARY': '',
        'NOISE_REDUCTION': False,
        'USE_CLIPBOARD': False,
        'NOISE_GATE_THRESHOLD': 0.0,
        'LOG_TRANSCRIPT_TEXT': False,
        'MAX_TYPED_CHARS': 1000,
    }
    if config_overrides:
        config_values.update(config_overrides)

    mocked_modules = {
        'keyboard': MagicMock(),
        'sounddevice': MagicMock(),
        'soundfile': MagicMock(),
        'pyperclip': MagicMock(),
        'pystray': MagicMock(),
        'PIL': MagicMock(),
        'PIL.Image': MagicMock(),
        'PIL.ImageDraw': MagicMock(),
        'noisereduce': MagicMock(),
        'faster_whisper': MagicMock(),
        'config': SimpleNamespace(**config_values),
    }

    with patch_dict_sys_modules(mocked_modules):
        if 'dictate' in sys.modules:
            del sys.modules['dictate']
        sys.path.insert(0, SRC_DIR)
        try:
            module = importlib.import_module('dictate')
        finally:
            sys.path.pop(0)
            if 'dictate' in sys.modules:
                del sys.modules['dictate']

    return module, mocked_modules


class patch_dict_sys_modules:
    """Minimal context manager to patch sys.modules for imports."""

    def __init__(self, entries):
        self._entries = entries
        self._previous = {}
        self._missing = set()

    def __enter__(self):
        for key, value in self._entries.items():
            if key in sys.modules:
                self._previous[key] = sys.modules[key]
            else:
                self._missing.add(key)
            sys.modules[key] = value

    def __exit__(self, exc_type, exc, tb):
        for key in self._entries:
            if key in self._previous:
                sys.modules[key] = self._previous[key]
            elif key in self._missing and key in sys.modules:
                del sys.modules[key]


def _prime_recording_state(module, samples=3200, amplitude=0.2):
    with module.STATE.lock:
        module.STATE.is_recording = True
        module.STATE.is_processing = False
        module.STATE.recording_start_time = time.time() - 1
        module.STATE.silence_flag = False
        module.STATE.recorded_frames = [np.ones((samples, 1), dtype=np.float32) * amplitude]


def test_start_recording_is_blocked_while_processing():
    module, _ = _import_dictate_module()
    with module.STATE.lock:
        module.STATE.is_processing = True
        module.STATE.is_recording = False

    started = module.start_recording()

    assert started is False
    assert module.STATE.is_recording is False


def test_stop_recording_processes_once_and_clears_processing():
    module, mocked = _import_dictate_module()
    _prime_recording_state(module)
    module.STATE.model = MagicMock()
    with patch.object(module.transcription_io, 'transcribe_audio_array', return_value='hello world') as transcribe_mock:
        module.stop_recording_and_transcribe()
        module.stop_recording_and_transcribe()
    transcribe_mock.assert_called_once()
    mocked['keyboard'].write.assert_called_once()
    assert module.STATE.is_processing is False
    assert module.STATE.is_recording is False


def test_start_recording_plays_ascending_start_tone():
    module, _ = _import_dictate_module()
    with module.STATE.lock:
        module.STATE.is_recording = False
        module.STATE.is_processing = False

    with patch.object(module, '_play_tone') as tone_mock:
        started = module.start_recording()

    assert started is True
    tone_mock.assert_called_once_with(800, 1000)


def test_start_recording_guard_paths_play_no_tone():
    module, _ = _import_dictate_module()
    with patch.object(module, '_play_tone') as tone_mock:
        with module.STATE.lock:
            module.STATE.is_recording = True
            module.STATE.is_processing = False
        assert module.start_recording() is False

        with module.STATE.lock:
            module.STATE.is_recording = False
            module.STATE.is_processing = True
        assert module.start_recording() is False

    tone_mock.assert_not_called()


def test_stop_recording_plays_descending_stop_tone_once():
    module, _ = _import_dictate_module()
    _prime_recording_state(module)
    module.STATE.model = MagicMock()

    with patch.object(module, '_play_tone') as tone_mock, \
         patch.object(module.transcription_io, 'transcribe_audio_array', return_value='hello world'):
        module.stop_recording_and_transcribe()
        module.stop_recording_and_transcribe()

    tone_mock.assert_called_once_with(1000, 800)


def test_stop_recording_truncates_output_to_max_typed_chars():
    module, mocked = _import_dictate_module({'MAX_TYPED_CHARS': 5})
    _prime_recording_state(module)
    module.STATE.model = MagicMock()
    with patch.object(module.transcription_io, 'transcribe_audio_array', return_value='abcdefghi'):
        module.stop_recording_and_transcribe()

    typed_text = mocked['keyboard'].write.call_args.args[0]
    assert typed_text == 'abcde'


def test_inject_text_writes_in_bursts_with_pauses():
    module, mocked = _import_dictate_module({'INJECT_CHUNK_CHARS': 4, 'INJECT_CHUNK_PAUSE_S': 0.0})

    module._inject_text('abcdefghij')

    calls = mocked['keyboard'].write.call_args_list
    assert [c.args[0] for c in calls] == ['abcd', 'efgh', 'ij']
    assert all(c.kwargs['delay'] == 0 for c in calls)


def test_inject_text_chunk_chars_zero_restores_legacy_per_char_throttle():
    module, mocked = _import_dictate_module({'INJECT_CHUNK_CHARS': 0})

    module._inject_text('abcdefghij')

    calls = mocked['keyboard'].write.call_args_list
    assert len(calls) == 1
    assert calls[0].args[0] == 'abcdefghij'
    assert calls[0].kwargs['delay'] == 0.01


def test_inject_text_chunk_defaults():
    module, _ = _import_dictate_module()
    assert module.INJECT_CHUNK_CHARS == 32
    assert abs(module.INJECT_CHUNK_PAUSE_S - 0.1) < 1e-9


def test_watchdog_release_fallback_triggers_stop_once():
    module, _ = _import_dictate_module()
    with module.STATE.lock:
        module.STATE.is_recording = True
        module.STATE.is_processing = False
        module.STATE.recording_start_time = time.time() - 1
    module.RECORDING_MONITOR_INTERVAL = 0.001
    module.STATE.shutdown_event.clear()
    module._is_hotkey_currently_pressed = MagicMock(return_value=False)

    stop_mock = MagicMock(side_effect=lambda: module.STATE.shutdown_event.set())
    module.stop_recording_and_transcribe = stop_mock

    module._recording_state_watchdog()

    stop_mock.assert_called_once()


def test_concurrent_release_watchdog_and_callback_paths_single_flight_transcription():
    module, mocked = _import_dictate_module()
    _prime_recording_state(module, samples=3200, amplitude=0.2)
    module.STATE.model = MagicMock()
    module.RECORDING_MONITOR_INTERVAL = 0.001
    module.STATE.shutdown_event.clear()
    module._is_hotkey_currently_pressed = MagicMock(return_value=False)
    errors = []

    with patch.object(module.transcription_io, 'transcribe_audio_array', return_value='race test') as transcribe_mock:
        def watchdog_runner():
            try:
                module._recording_state_watchdog()
            except Exception as exc:
                errors.append(exc)

        def callback_spammer():
            frame = np.ones((16, 1), dtype=np.float32) * 0.25
            for _ in range(100):
                try:
                    module.audio_callback(frame, 16, None, None)
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.0005)

        watch_thread = threading.Thread(target=watchdog_runner)
        callback_thread = threading.Thread(target=callback_spammer)
        watch_thread.start()
        callback_thread.start()
        time.sleep(0.01)
        module.on_hotkey_release()
        time.sleep(0.02)
        module.STATE.shutdown_event.set()
        watch_thread.join(timeout=1)
        callback_thread.join(timeout=1)

    assert errors == []
    assert transcribe_mock.call_count == 1
    assert mocked['keyboard'].write.call_count == 1
    assert module.STATE.is_processing is False


def test_processing_flag_clears_on_concatenate_failure():
    module, _ = _import_dictate_module()
    with module.STATE.lock:
        module.STATE.is_recording = True
        module.STATE.is_processing = False
        module.STATE.recording_start_time = time.time() - 1
        module.STATE.recorded_frames = [np.zeros((10, 1), dtype=np.float32), np.zeros((10,), dtype=np.float32)]

    module.stop_recording_and_transcribe()

    assert module.STATE.is_processing is False
    assert module.STATE.is_recording is False


def test_processing_flag_clears_on_short_audio_return():
    module, _ = _import_dictate_module()
    _prime_recording_state(module, samples=200, amplitude=0.2)

    module.stop_recording_and_transcribe()

    assert module.STATE.is_processing is False
    assert module.STATE.is_recording is False


def test_processing_flag_clears_on_noise_gate_quiet_return():
    module, _ = _import_dictate_module({'NOISE_GATE_THRESHOLD': 0.5})
    _prime_recording_state(module, samples=3200, amplitude=0.01)

    module.stop_recording_and_transcribe()

    assert module.STATE.is_processing is False
    assert module.STATE.is_recording is False


def test_noise_gate_allows_low_rms_with_strong_peaks():
    module, mocked = _import_dictate_module({'NOISE_GATE_THRESHOLD': 0.1})
    frame = np.zeros((3200, 1), dtype=np.float32)
    frame[100, 0] = 0.4  # Low RMS, but clear speech-like peak above peak gate.
    with module.STATE.lock:
        module.STATE.is_recording = True
        module.STATE.is_processing = False
        module.STATE.recording_start_time = time.time() - 1
        module.STATE.recorded_frames = [frame]
    module.STATE.model = MagicMock()

    with patch.object(module.transcription_io, 'transcribe_audio_array', return_value='peak speech') as transcribe_mock:
        module.stop_recording_and_transcribe()

    transcribe_mock.assert_called_once()
    mocked['keyboard'].write.assert_called_once()
    assert module.STATE.is_processing is False
    assert module.STATE.is_recording is False


def test_processing_flag_clears_on_transcription_exception():
    module, _ = _import_dictate_module()
    _prime_recording_state(module, samples=3200, amplitude=0.2)
    module.STATE.model = MagicMock()

    with patch.object(module.transcription_io, 'transcribe_audio_array', side_effect=RuntimeError('boom')):
        module.stop_recording_and_transcribe()

    assert module.STATE.is_processing is False
    assert module.STATE.is_recording is False


def test_load_wake_word_model_downloads_missing_assets_and_retries():
    module, _ = _import_dictate_module()
    model_instances = [FileNotFoundError('missing model'), MagicMock(models={'hey_jarvis_v0.1': object()})]
    download_mock = MagicMock()

    def fake_model(**kwargs):
        result = model_instances.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.dict(sys.modules, {
        'openwakeword': MagicMock(),
        'openwakeword.model': SimpleNamespace(Model=fake_model),
        'openwakeword.utils': SimpleNamespace(download_models=download_mock),
    }):
        model = module._load_wake_word_model()

    assert model is not None
    download_mock.assert_called_once_with([module.WAKE_WORD_MODEL])


def test_switch_and_reopen_paths_obey_shared_lock_contention():
    module, _ = _import_dictate_module()
    manager = MagicMock()
    module._get_stream_manager = MagicMock(return_value=manager)
    with module.STATE.lock:
        module.STATE.is_recording = False
        module.STATE.is_processing = False
    module._switch_lock.acquire()
    try:
        reopened = module._reopen_audio_stream('test-contention')
        module.switch_audio_device(1, 'Mic')
    finally:
        module._switch_lock.release()

    assert reopened is False
    manager.reopen.assert_not_called()
    manager.switch.assert_not_called()


def test_config_numeric_strings_are_coerced_for_startup_safety():
    module, _ = _import_dictate_module({
        'NOISE_GATE_THRESHOLD': '0.0057',
        'MAX_TYPED_CHARS': '42',
        'BEAM_SIZE': '3',
    })
    assert abs(module.NOISE_GATE_THRESHOLD - 0.0057) < 1e-9
    assert module.MAX_TYPED_CHARS == 42
    assert module.BEAM_SIZE == 3


def test_beam_size_config_reaches_transcription_call():
    module, _ = _import_dictate_module({'BEAM_SIZE': 1})
    _prime_recording_state(module)
    module.STATE.model = MagicMock()

    with patch.object(module.transcription_io, 'transcribe_audio_array', return_value='hi') as transcribe_mock:
        module.stop_recording_and_transcribe()

    assert transcribe_mock.call_args.kwargs['beam_size'] == 1


def test_check_single_instance_retries_mutex_for_restart_handoff():
    module, _ = _import_dictate_module()
    lock_path = os.path.join(tempfile.gettempdir(), 'voice-dictation-test-restart.lock')
    if os.path.exists(lock_path):
        os.unlink(lock_path)
    module.LOCK_FILE = lock_path

    try:
        with patch.object(module, '_acquire_single_instance_mutex', side_effect=[False, False, True]) as acquire_mock, \
             patch.object(module.time, 'sleep') as sleep_mock:
            module.check_single_instance(retry_seconds=2, poll_seconds=0.01)
    finally:
        if os.path.exists(lock_path):
            os.unlink(lock_path)

    assert acquire_mock.call_count == 3
    assert sleep_mock.call_count == 2


def test_on_tray_restart_uses_restarting_cleanup_status():
    module, _ = _import_dictate_module()
    icon = MagicMock()

    with patch('subprocess.Popen') as popen_mock, \
         patch.object(module, 'cleanup_resources') as cleanup_mock:
        module.on_tray_restart(icon, None)

    popen_mock.assert_called_once()
    icon.stop.assert_called_once()
    cleanup_mock.assert_called_once()
    kwargs = cleanup_mock.call_args.kwargs
    assert kwargs['shutdown_status'] == 'restarting'
    assert kwargs['shutdown_reason'] == 'tray_restart_requested'
    assert 'restart_parent_pid' in kwargs['extra_details']
