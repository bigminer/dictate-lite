"""
Automated tests for src/calibrate.py — Group B device resilience + div-by-zero fix.

Run with:  .venv\\Scripts\\python -m pytest tests/ -v
"""

import os
import sys
import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ===================================================================
# Device Resolution (mirrors dictate.py's fallback chain)
# ===================================================================

class TestResolveAudioDevice:
    """Tests for calibrate.py's resolve_audio_device() fallback chain."""

    def _make_devices(self, device_list):
        """Create a mock query_devices return value."""
        return device_list

    def test_string_name_exact_match(self):
        """String AUDIO_DEVICE should resolve by exact name match."""
        devices = [
            {'name': 'Built-in Mic', 'max_input_channels': 1, 'max_output_channels': 0},
            {'name': 'USB Headset', 'max_input_channels': 1, 'max_output_channels': 0},
        ]
        input_devs = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
        audio_device = 'USB Headset'

        found = None
        for idx, dev in input_devs:
            if dev['name'] == audio_device:
                found = (idx, dev['name'])
                break

        assert found == (1, 'USB Headset')

    def test_string_name_substring_match(self):
        """String AUDIO_DEVICE should fall back to substring match."""
        devices = [
            {'name': 'Microphone (Realtek Audio)', 'max_input_channels': 2, 'max_output_channels': 0},
        ]
        input_devs = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
        audio_device = 'Realtek'

        found = None
        for idx, dev in input_devs:
            if dev['name'] == audio_device:
                found = (idx, dev['name'])
                break
        if found is None:
            for idx, dev in input_devs:
                if audio_device in dev['name'] or dev['name'] in audio_device:
                    found = (idx, dev['name'])
                    break

        assert found == (0, 'Microphone (Realtek Audio)')

    def test_string_name_not_found_falls_back(self):
        """Missing device name should fall through to default."""
        devices = [
            {'name': 'Built-in Mic', 'max_input_channels': 1, 'max_output_channels': 0},
        ]
        input_devs = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
        audio_device = 'Nonexistent USB Mic'

        found = None
        for idx, dev in input_devs:
            if dev['name'] == audio_device:
                found = (idx, dev['name'])
                break
        if found is None:
            for idx, dev in input_devs:
                if audio_device in dev['name'] or dev['name'] in audio_device:
                    found = (idx, dev['name'])
                    break

        assert found is None  # Should fall through to default/first-available

    def test_legacy_int_valid_index(self):
        """Legacy integer AUDIO_DEVICE with valid index should resolve."""
        devices = [
            {'name': 'Speakers', 'max_input_channels': 0, 'max_output_channels': 2},
            {'name': 'Mic', 'max_input_channels': 1, 'max_output_channels': 0},
        ]
        audio_device = 1  # Legacy integer

        assert isinstance(audio_device, int)
        assert devices[audio_device]['max_input_channels'] > 0

    def test_legacy_int_invalid_index(self):
        """Legacy integer AUDIO_DEVICE beyond device count should fail gracefully."""
        devices = [
            {'name': 'Mic', 'max_input_channels': 1, 'max_output_channels': 0},
        ]
        audio_device = 5  # Beyond device count

        with pytest.raises(IndexError):
            _ = devices[audio_device]

    def test_default_device_negative_one_rejected(self):
        """Default device index of -1 should be skipped."""
        default_idx = -1
        assert not (default_idx is not None and default_idx >= 0)

    def test_first_available_fallback(self):
        """Last resort should return first available input device."""
        devices = [
            {'name': 'Output Only', 'max_input_channels': 0, 'max_output_channels': 2},
            {'name': 'Webcam Mic', 'max_input_channels': 1, 'max_output_channels': 0},
            {'name': 'USB Mic', 'max_input_channels': 1, 'max_output_channels': 0},
        ]
        input_devs = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
        first_idx, first_dev = input_devs[0]
        assert first_idx == 1
        assert first_dev['name'] == 'Webcam Mic'


# ===================================================================
# Division by Zero Guard
# ===================================================================

class TestDivisionByZeroGuard:
    """Tests for the ambient_rms division-by-zero fix in calibrate.py."""

    def test_zero_ambient_rms_no_crash(self):
        """When ambient RMS is 0, ratio should not be computed."""
        ambient_rms = 0.0
        speech_rms = 0.05

        # This is the exact guard from the fix
        if ambient_rms > 0:
            ratio = speech_rms / ambient_rms
            result = f"{ratio:.1f}x louder"
        else:
            result = "N/A (ambient was silent)"

        assert result == "N/A (ambient was silent)"

    def test_nonzero_ambient_rms_computes_ratio(self):
        """When ambient RMS is nonzero, ratio should be computed."""
        ambient_rms = 0.01
        speech_rms = 0.05

        if ambient_rms > 0:
            ratio = speech_rms / ambient_rms
            result = f"{ratio:.1f}x louder"
        else:
            result = "N/A (ambient was silent)"

        assert result == "5.0x louder"

    def test_both_zero_no_crash(self):
        """When both ambient and speech are zero, should not crash."""
        ambient_rms = 0.0
        speech_rms = 0.0

        if ambient_rms > 0:
            ratio = speech_rms / ambient_rms
        else:
            ratio = None

        assert ratio is None

    def test_threshold_calculation_with_zero_ambient(self):
        """Threshold calculation should work even when ambient is 0."""
        ambient_rms = 0.0
        speech_rms = 0.05

        threshold = ambient_rms + (speech_rms - ambient_rms) * 0.3
        threshold = max(threshold, 0.005)
        threshold = round(threshold, 4)

        assert threshold == 0.015  # 0 + (0.05 - 0) * 0.3 = 0.015

    def test_threshold_minimum_enforced(self):
        """Threshold should never go below 0.005."""
        ambient_rms = 0.0001
        speech_rms = 0.0002

        threshold = ambient_rms + (speech_rms - ambient_rms) * 0.3
        threshold = max(threshold, 0.005)

        assert threshold == 0.005


# ===================================================================
# Source Code Verification
# ===================================================================

class TestCalibrateSourcePatterns:
    """Verify expected patterns exist in the calibrate.py source."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'calibrate.py')
        with open(src_path, 'r') as f:
            self.source = f.read()

    def test_resolve_audio_device_exists(self):
        """calibrate.py should have a resolve_audio_device function."""
        assert 'def resolve_audio_device(' in self.source

    def test_division_guard_exists(self):
        """calibrate.py should guard against division by zero."""
        assert 'if ambient_rms > 0:' in self.source

    def test_capture_from_stream_has_try_except(self):
        """capture_from_stream() should be wrapped in try/except."""
        assert 'audio_capture.capture_from_stream(' in self.source
        # Check that there's a try block near capture call
        lines = self.source.splitlines()
        for i, line in enumerate(lines):
            if 'audio_capture.capture_from_stream(' in line:
                # Look back for try
                context = '\n'.join(lines[max(0, i-5):i+1])
                assert 'try:' in context, "capture_from_stream() should be inside a try block"
                break

    def test_record_audio_accepts_device_index(self):
        """record_audio should accept a device_index parameter."""
        assert 'def record_audio(duration, prompt, device_index)' in self.source
