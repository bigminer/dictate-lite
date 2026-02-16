"""Behavioral runtime guard tests for src/dictate.py."""

from __future__ import annotations

import importlib
import os
import sys
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


def test_stop_recording_truncates_output_to_max_typed_chars():
    module, mocked = _import_dictate_module({'MAX_TYPED_CHARS': 5})
    _prime_recording_state(module)
    module.STATE.model = MagicMock()
    with patch.object(module.transcription_io, 'transcribe_audio_array', return_value='abcdefghi'):
        module.stop_recording_and_transcribe()

    typed_text = mocked['keyboard'].write.call_args.args[0]
    assert typed_text == 'abcde'


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
