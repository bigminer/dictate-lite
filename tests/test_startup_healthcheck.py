"""Behavioral tests for src/startup_healthcheck.py."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

import startup_healthcheck as healthcheck


def _default_cfg():
    return {
        'HOTKEY': 'alt+f',
        'MODEL_SIZE': 'small',
        'LANGUAGE': 'en',
        'DEVICE': 'cpu',
        'COMPUTE_TYPE': 'int8',
        'AUDIO_DEVICE': None,
        'AUDIO_DEVICE_HOSTAPI': None,
        'AUDIO_DEVICE_INDEX': None,
        'AUDIO_DEVICE_UID': None,
    }


def test_parse_args_sets_expected_flags():
    args = healthcheck.parse_args(['--healthcheck-only'])
    assert args.healthcheck_only is True
    assert args.skip_healthcheck is False

    args = healthcheck.parse_args(['--skip-healthcheck'])
    assert args.skip_healthcheck is True
    assert args.healthcheck_only is False


def test_resolve_device_returns_none_when_no_match():
    cfg = _default_cfg()
    input_devices = [(1, 'Mic', 'Windows WASAPI', 'uid-1')]
    with patch.object(healthcheck.audio_identity, 'resolve_preferred_input_device', return_value=(None, None, None, None)):
        resolved = healthcheck.resolve_device(cfg, input_devices)
    assert resolved is None


def test_run_healthcheck_fails_when_no_input_devices():
    with patch.object(healthcheck, 'load_config', return_value=_default_cfg()), \
         patch.object(healthcheck, 'load_runtime_state', return_value={}), \
         patch.object(healthcheck, 'enumerate_input_devices', return_value=[]):
        assert healthcheck.run_healthcheck() is False


def test_run_healthcheck_succeeds_on_first_phrase_match():
    cfg = _default_cfg()
    audio = np.ones((16000,), dtype=np.float32) * 0.1
    with patch.object(healthcheck, 'load_config', return_value=cfg), \
         patch.object(healthcheck, 'load_runtime_state', return_value={}), \
         patch.object(healthcheck, 'enumerate_input_devices', return_value=[(1, 'Mic', 'Windows WASAPI', 'uid-1')]), \
         patch.object(healthcheck, 'resolve_device', return_value=(1, 'Mic', 'Windows WASAPI', 'uid-1')), \
         patch.object(healthcheck, 'stream_probe'), \
         patch.object(healthcheck, 'record_phrase', return_value=audio), \
         patch.object(healthcheck, 'transcribe_audio', return_value='check 1 2 3') as transcribe_mock, \
         patch('builtins.input', side_effect=['']), \
         patch.object(healthcheck.time, 'sleep'):
        assert healthcheck.run_healthcheck() is True
    transcribe_mock.assert_called_once()


def test_run_healthcheck_fails_after_phrase_mismatch_attempts():
    cfg = _default_cfg()
    audio = np.ones((16000,), dtype=np.float32) * 0.1
    with patch.object(healthcheck, 'load_config', return_value=cfg), \
         patch.object(healthcheck, 'load_runtime_state', return_value={}), \
         patch.object(healthcheck, 'enumerate_input_devices', return_value=[(1, 'Mic', 'Windows WASAPI', 'uid-1')]), \
         patch.object(healthcheck, 'resolve_device', return_value=(1, 'Mic', 'Windows WASAPI', 'uid-1')), \
         patch.object(healthcheck, 'stream_probe'), \
         patch.object(healthcheck, 'record_phrase', return_value=audio), \
         patch.object(healthcheck, 'transcribe_audio', return_value='not the phrase') as transcribe_mock, \
         patch('builtins.input', side_effect=['', '', '']), \
         patch.object(healthcheck.time, 'sleep'):
        assert healthcheck.run_healthcheck() is False
    assert transcribe_mock.call_count == healthcheck.MAX_PHRASE_ATTEMPTS


def test_run_healthcheck_returns_false_when_input_unavailable():
    cfg = _default_cfg()
    with patch.object(healthcheck, 'load_config', return_value=cfg), \
         patch.object(healthcheck, 'load_runtime_state', return_value={}), \
         patch.object(healthcheck, 'enumerate_input_devices', return_value=[(1, 'Mic', 'Windows WASAPI', 'uid-1')]), \
         patch.object(healthcheck, 'resolve_device', return_value=(1, 'Mic', 'Windows WASAPI', 'uid-1')), \
         patch.object(healthcheck, 'stream_probe'), \
         patch('builtins.input', side_effect=EOFError()):
        assert healthcheck.run_healthcheck() is False
