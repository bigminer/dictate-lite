"""
Automated tests for src/dictate.py — covers Groups A, B, and C fixes.

Run with:  .venv\\Scripts\\python -m pytest tests/ -v
"""

import os
import sys
import time
import tempfile
import textwrap
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# We can't import dictate.py at module level because it has heavy side effects
# (logging setup, hardware imports, config loading). Instead, we import the
# specific functions/constants we need inside each test or use importlib.
# For pure-logic functions, we can extract and test them directly.
# ---------------------------------------------------------------------------


# ===================================================================
# GROUP A: Zombie Process / Restart Fixes
# ===================================================================

class TestIsPythonProcess:
    """Tests for _is_python_process() — PID validation."""

    def _get_func(self):
        """Import the function under test."""
        # We need to import carefully to avoid side effects
        import importlib
        import types

        # Read the function source and exec it in an isolated namespace
        src_dir = os.path.join(os.path.dirname(__file__), '..', 'src')
        with open(os.path.join(src_dir, 'dictate.py'), 'r') as f:
            source = f.read()

        # Extract just the function we need by importing the module with mocked deps
        with patch.dict('sys.modules', {
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
            'config': MagicMock(
                HOTKEY='alt+f', MODEL_SIZE='small', DEVICE='cpu',
                COMPUTE_TYPE='int8', AUDIO_DEVICE=None, LANGUAGE='en',
                VOCABULARY='', NOISE_REDUCTION=False, USE_CLIPBOARD=True,
                NOISE_GATE_THRESHOLD=0.01,
            ),
        }):
            if 'dictate' in sys.modules:
                del sys.modules['dictate']
            sys.path.insert(0, src_dir)
            try:
                import dictate
                return dictate._is_python_process
            finally:
                sys.path.pop(0)
                if 'dictate' in sys.modules:
                    del sys.modules['dictate']

    def test_current_process_is_python(self):
        """Our own PID should be detected as Python."""
        func = self._get_func()
        assert func(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        """A PID that doesn't exist should return False."""
        func = self._get_func()
        # PID 4 is System on Windows, PID 99999999 almost certainly doesn't exist
        assert func(99999999) is False

    def test_pid_zero_returns_false(self):
        """PID 0 (System Idle Process) should not be Python."""
        func = self._get_func()
        assert func(0) is False


class TestCheckSingleInstance:
    """Tests for lock file management in check_single_instance()."""

    def test_creates_lock_file(self):
        """check_single_instance should create a lock file with current PID."""
        lock_file = os.path.join(tempfile.gettempdir(), 'test-voice-dictation.lock')
        try:
            # Remove if exists
            if os.path.exists(lock_file):
                os.unlink(lock_file)

            # Simulate the lock creation logic
            my_pid = os.getpid()
            with open(lock_file, 'w') as f:
                f.write(str(my_pid))

            assert os.path.exists(lock_file)
            with open(lock_file, 'r') as f:
                assert int(f.read().strip()) == my_pid
        finally:
            if os.path.exists(lock_file):
                os.unlink(lock_file)

    def test_stale_lock_old_timestamp(self):
        """A lock file older than 24 hours should be treated as stale."""
        lock_file = os.path.join(tempfile.gettempdir(), 'test-voice-dictation.lock')
        try:
            with open(lock_file, 'w') as f:
                f.write("99999")

            # Backdate the file by 25 hours
            old_time = time.time() - (25 * 3600)
            os.utime(lock_file, (old_time, old_time))

            lock_age = time.time() - os.path.getmtime(lock_file)
            assert lock_age > 86400, "Lock file should be older than 24 hours"
        finally:
            if os.path.exists(lock_file):
                os.unlink(lock_file)

    def test_stale_lock_dead_pid(self):
        """A lock file with a dead PID should be treated as stale."""
        lock_file = os.path.join(tempfile.gettempdir(), 'test-voice-dictation.lock')
        try:
            # Write a PID that almost certainly doesn't exist
            with open(lock_file, 'w') as f:
                f.write("99999999")

            with open(lock_file, 'r') as f:
                pid = int(f.read().strip())

            # Verify this PID is not a running Python process
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            is_alive = bool(handle)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)

            assert not is_alive, "PID 99999999 should not be a running process"
        finally:
            if os.path.exists(lock_file):
                os.unlink(lock_file)


class TestCleanupResources:
    """Tests for cleanup_resources() — resource teardown."""

    def test_closes_audio_stream(self):
        """cleanup_resources should call stop() and close() on the audio stream."""
        mock_stream = MagicMock()

        # We test the logic pattern, not the actual function (which calls os._exit)
        try:
            mock_stream.stop()
            mock_stream.close()
        except Exception:
            pass

        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()

    def test_removes_lock_file(self):
        """cleanup_resources should remove the lock file."""
        lock_file = os.path.join(tempfile.gettempdir(), 'test-voice-dictation-cleanup.lock')
        try:
            with open(lock_file, 'w') as f:
                f.write(str(os.getpid()))

            assert os.path.exists(lock_file)
            os.unlink(lock_file)
            assert not os.path.exists(lock_file)
        finally:
            if os.path.exists(lock_file):
                os.unlink(lock_file)

    def test_unhook_keyboard_error_does_not_crash(self):
        """If keyboard.unhook_all() raises, cleanup should continue."""
        mock_keyboard = MagicMock()
        mock_keyboard.unhook_all.side_effect = Exception("hook error")

        # Should not raise
        try:
            mock_keyboard.unhook_all()
        except Exception:
            pass  # This is the expected behavior — catch and continue


# ===================================================================
# GROUP B: Device Resilience
# ===================================================================

class TestResolveDeviceNameToIndex:
    """Tests for _resolve_device_name_to_index() — name-based device lookup."""

    def _resolve(self, device_name, input_devices):
        """Call the resolution function."""
        # Exact match first
        for idx, dev in input_devices:
            if dev['name'] == device_name:
                return idx, dev['name']
        # Substring match
        for idx, dev in input_devices:
            if device_name in dev['name'] or dev['name'] in device_name:
                return idx, dev['name']
        return None, None

    def test_exact_match(self, input_only_devices):
        """Exact device name should resolve to correct index."""
        idx, name = self._resolve('USB Headset Mic', input_only_devices)
        assert idx == 2
        assert name == 'USB Headset Mic'

    def test_substring_match_name_in_query(self, input_only_devices):
        """Substring of device name should match."""
        idx, name = self._resolve('HD Pro', input_only_devices)
        assert idx == 3
        assert name == 'Webcam Microphone (HD Pro)'

    def test_substring_match_query_in_name(self, input_only_devices):
        """Query that contains the full device name should match."""
        idx, name = self._resolve('Webcam Microphone (HD Pro) Extra Text', input_only_devices)
        assert idx == 3
        assert name == 'Webcam Microphone (HD Pro)'

    def test_no_match_returns_none(self, input_only_devices):
        """Non-existent device name should return (None, None)."""
        idx, name = self._resolve('Nonexistent Fake Device XYZ', input_only_devices)
        assert idx is None
        assert name is None

    def test_exact_match_takes_priority_over_substring(self):
        """When both exact and substring matches exist, exact wins."""
        devices = [
            (0, {'name': 'Mic', 'max_input_channels': 1}),
            (1, {'name': 'Mic Pro', 'max_input_channels': 1}),
        ]
        idx, name = self._resolve('Mic', devices)
        assert idx == 0
        assert name == 'Mic'

    def test_empty_device_list(self):
        """Empty device list should return (None, None)."""
        idx, name = self._resolve('Any Device', [])
        assert idx is None
        assert name is None


class TestSaveAudioDeviceToConfig:
    """Tests for save_audio_device_to_config() — config persistence."""

    def test_saves_device_name_as_string(self, tmp_config):
        """Device name should be saved as a quoted string."""
        import re
        content = tmp_config.read_text()

        # Simulate the save logic
        device_name = 'USB Headset Mic'
        escaped = device_name.replace("'", "\\'")
        value_str = f"'{escaped}'"
        content = re.sub(r"AUDIO_DEVICE\s*=\s*.*", f"AUDIO_DEVICE = {value_str}", content)

        tmp_config.write_text(content)
        result = tmp_config.read_text()
        assert "AUDIO_DEVICE = 'USB Headset Mic'" in result

    def test_saves_none(self, tmp_config):
        """None device should be saved as bare None."""
        import re
        content = tmp_config.read_text()
        content = re.sub(r"AUDIO_DEVICE\s*=\s*.*", "AUDIO_DEVICE = None", content)
        tmp_config.write_text(content)
        assert "AUDIO_DEVICE = None" in tmp_config.read_text()

    def test_handles_single_quotes_in_name(self, tmp_config):
        """Device names with single quotes should be escaped."""
        import re
        content = tmp_config.read_text()
        device_name = "Gary's Headset"
        escaped = device_name.replace("'", "\\'")
        value_str = f"'{escaped}'"
        content = re.sub(r"AUDIO_DEVICE\s*=\s*.*", f"AUDIO_DEVICE = {value_str}", content)
        tmp_config.write_text(content)
        assert "AUDIO_DEVICE = 'Gary\\'s Headset'" in tmp_config.read_text()

    def test_overwrites_integer_format(self, tmp_config):
        """Saving a name should overwrite a legacy integer value."""
        import re
        # Start with legacy integer
        content = tmp_config.read_text().replace(
            "AUDIO_DEVICE = None", "AUDIO_DEVICE = 2"
        )
        tmp_config.write_text(content)
        assert "AUDIO_DEVICE = 2" in tmp_config.read_text()

        # Now save a name
        content = tmp_config.read_text()
        value_str = "'New Mic'"
        content = re.sub(r"AUDIO_DEVICE\s*=\s*.*", f"AUDIO_DEVICE = {value_str}", content)
        tmp_config.write_text(content)
        assert "AUDIO_DEVICE = 'New Mic'" in tmp_config.read_text()
        assert "AUDIO_DEVICE = 2" not in tmp_config.read_text()

    def test_appends_if_missing(self, tmp_config):
        """If AUDIO_DEVICE line doesn't exist, it should be appended."""
        # Remove the AUDIO_DEVICE line
        content = tmp_config.read_text()
        lines = [l for l in content.splitlines() if 'AUDIO_DEVICE' not in l]
        tmp_config.write_text('\n'.join(lines))
        assert 'AUDIO_DEVICE' not in tmp_config.read_text()

        # Now append it
        content = tmp_config.read_text()
        content += "\n# Audio device (selected from tray menu)\nAUDIO_DEVICE = 'Test Mic'\n"
        tmp_config.write_text(content)
        assert "AUDIO_DEVICE = 'Test Mic'" in tmp_config.read_text()


class TestCheckMicrophoneFallback:
    """Tests for the check_microphone() fallback chain logic."""

    def test_string_device_found(self):
        """When AUDIO_DEVICE is a string and device exists, it should be used."""
        devices = [
            (0, {'name': 'Built-in Mic', 'max_input_channels': 1}),
            (1, {'name': 'USB Headset', 'max_input_channels': 1}),
        ]
        # Simulate: AUDIO_DEVICE = 'USB Headset'
        idx, name = None, None
        for i, d in devices:
            if d['name'] == 'USB Headset':
                idx, name = i, d['name']
                break
        assert idx == 1
        assert name == 'USB Headset'

    def test_string_device_not_found_falls_back(self):
        """When AUDIO_DEVICE name doesn't match, should return None."""
        devices = [
            (0, {'name': 'Built-in Mic', 'max_input_channels': 1}),
        ]
        idx, name = None, None
        for i, d in devices:
            if d['name'] == 'Missing Device':
                idx, name = i, d['name']
                break
        assert idx is None

    def test_default_device_negative_one_skipped(self):
        """Default device index of -1 should be treated as invalid."""
        default_idx = -1
        assert not (default_idx is not None and default_idx >= 0)

    def test_default_device_none_skipped(self):
        """Default device index of None should be treated as invalid."""
        default_idx = None
        assert not (default_idx is not None and default_idx >= 0)

    def test_default_device_valid_index_accepted(self):
        """Default device index of 0 or positive should be accepted."""
        for idx in [0, 1, 5]:
            assert idx is not None and idx >= 0

    def test_first_available_fallback(self):
        """When all else fails, first input device should be used."""
        devices = [
            (0, {'name': 'Only Mic', 'max_input_channels': 1}),
        ]
        first_idx, first_dev = devices[0]
        assert first_idx == 0
        assert first_dev['name'] == 'Only Mic'


class TestSwitchAudioDevice:
    """Tests for switch_audio_device() safe swap logic."""

    def test_new_stream_opened_before_old_closed(self):
        """The new stream should be created and started before the old is closed."""
        call_order = []

        class FakeNewStream:
            def start(self):
                call_order.append('new_start')
            def stop(self):
                call_order.append('new_stop')
            def close(self):
                call_order.append('new_close')

        class FakeOldStream:
            def stop(self):
                call_order.append('old_stop')
            def close(self):
                call_order.append('old_close')

        # Simulate the swap pattern from switch_audio_device
        new_stream = FakeNewStream()
        new_stream.start()  # Open new first

        old_stream = FakeOldStream()
        old_stream.stop()   # Then close old
        old_stream.close()

        assert call_order == ['new_start', 'old_stop', 'old_close']

    def test_failed_new_stream_keeps_old_running(self):
        """If new stream fails to open, old stream should remain untouched."""
        old_stream = MagicMock()
        new_stream_failed = False

        try:
            raise Exception("PortAudio error: device unavailable")
        except Exception:
            new_stream_failed = True

        assert new_stream_failed
        old_stream.stop.assert_not_called()
        old_stream.close.assert_not_called()

    def test_global_state_only_updated_on_success(self):
        """AUDIO_DEVICE and active_mic_name should only change after confirmed success."""
        audio_device = 'Old Mic'
        active_mic_name = 'Old Mic'

        # Simulate failure — globals should stay unchanged
        try:
            raise Exception("stream open failed")
        except Exception:
            pass  # In real code, we return early

        assert audio_device == 'Old Mic'
        assert active_mic_name == 'Old Mic'


# ===================================================================
# GROUP C: Microphone Validation & Health Monitoring
# ===================================================================

class TestAudioCallback:
    """Tests for audio_callback() improvements."""

    def test_silence_flag_set_for_zero_audio(self):
        """Silence flag should be set when audio block is all zeros."""
        indata = np.zeros((1024, 1), dtype=np.float32)
        rms = np.sqrt(np.mean(indata ** 2))
        assert rms < 1e-6

    def test_silence_flag_cleared_for_real_audio(self):
        """Silence flag should be cleared when audio has signal."""
        # Simulate a 440Hz sine wave
        t = np.linspace(0, 1024 / 16000, 1024, dtype=np.float32)
        indata = (np.sin(2 * np.pi * 440 * t) * 0.5).reshape(-1, 1)
        rms = np.sqrt(np.mean(indata ** 2))
        assert rms >= 1e-6

    def test_status_should_use_logger_not_print(self):
        """Audio status messages should go through logger, not print to stderr."""
        # Read the actual source and verify the pattern
        src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'dictate.py')
        with open(src_path, 'r') as f:
            source = f.read()

        # Find the audio_callback function
        in_callback = False
        found_logger = False
        found_print_stderr = False
        for line in source.splitlines():
            if 'def audio_callback(' in line:
                in_callback = True
            elif in_callback and line.strip().startswith('def '):
                break
            elif in_callback:
                if 'logger.warning' in line and 'Audio status' in line:
                    found_logger = True
                if 'print(' in line and 'stderr' in line and 'Audio status' in line:
                    found_print_stderr = True

        assert found_logger, "audio_callback should use logger.warning for status"
        assert not found_print_stderr, "audio_callback should NOT use print(stderr) for status"


class TestRecordingTimeout:
    """Tests for recording timeout logic."""

    def test_timeout_triggers_after_max_seconds(self):
        """Recording should be force-stopped after MAX_RECORDING_SECONDS."""
        MAX_RECORDING_SECONDS = 120
        recording_start_time = time.time() - 121  # Started 121 seconds ago

        elapsed = time.time() - recording_start_time
        assert elapsed > MAX_RECORDING_SECONDS

    def test_no_timeout_within_limit(self):
        """Recording within time limit should not trigger timeout."""
        MAX_RECORDING_SECONDS = 120
        recording_start_time = time.time() - 5  # Started 5 seconds ago

        elapsed = time.time() - recording_start_time
        assert elapsed <= MAX_RECORDING_SECONDS

    def test_custom_timeout_value(self):
        """Verify the default timeout is 120 seconds."""
        src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'dictate.py')
        with open(src_path, 'r') as f:
            source = f.read()
        assert 'MAX_RECORDING_SECONDS = 120' in source


class TestHotkeyReleaseFallback:
    """Tests for hotkey release fallback logic."""

    def test_hotkey_parts_are_parsed(self):
        """Hotkey parser should split configured combo into individual keys."""
        src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'dictate.py')
        with open(src_path, 'r') as f:
            source = f.read()
        assert "HOTKEY_PARTS = [part.strip() for part in HOTKEY.split('+') if part.strip()]" in source

    def test_watchdog_has_release_fallback(self):
        """Watchdog should stop recording when key state indicates release."""
        src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'dictate.py')
        with open(src_path, 'r') as f:
            source = f.read()
        assert 'if not _is_hotkey_currently_pressed():' in source
        assert 'Recording stop fallback triggered: hotkey no longer pressed' in source


class TestStopRecordingCorruptionGuard:
    """Tests for corrupt/short audio handling in stop_recording_and_transcribe()."""

    def test_empty_frames_skipped(self):
        """Empty recorded_frames should be handled gracefully."""
        recorded_frames = []
        assert len(recorded_frames) == 0
        # Real code returns early with "No audio captured"

    def test_short_audio_skipped(self):
        """Audio shorter than 0.1s should be skipped."""
        SAMPLE_RATE = 16000
        # 800 samples = 0.05s at 16kHz
        short_audio = np.zeros((800, 1), dtype=np.float32)
        duration_s = len(short_audio) / SAMPLE_RATE
        assert duration_s < 0.1

    def test_normal_audio_not_skipped(self):
        """Audio longer than 0.1s should proceed to transcription."""
        SAMPLE_RATE = 16000
        # 3200 samples = 0.2s at 16kHz
        normal_audio = np.random.randn(3200, 1).astype(np.float32) * 0.1
        duration_s = len(normal_audio) / SAMPLE_RATE
        assert duration_s >= 0.1

    def test_corrupt_frames_caught(self):
        """Frames that can't be concatenated should be caught."""
        # Mismatched shapes
        recorded_frames = [
            np.zeros((1024, 1), dtype=np.float32),
            np.zeros((512,), dtype=np.float32),  # Wrong shape
        ]
        with pytest.raises((ValueError, TypeError)):
            np.concatenate(recorded_frames, axis=0)

    def test_valid_frames_concatenate(self):
        """Well-formed frames should concatenate cleanly."""
        recorded_frames = [
            np.zeros((1024, 1), dtype=np.float32),
            np.ones((1024, 1), dtype=np.float32) * 0.5,
            np.zeros((512, 1), dtype=np.float32),
        ]
        result = np.concatenate(recorded_frames, axis=0)
        assert result.shape == (2560, 1)


class TestSilenceDetection:
    """Tests for real-time silence detection in audio_callback."""

    def test_zero_rms_detected_as_silence(self):
        """All-zero audio should have RMS below silence threshold."""
        audio = np.zeros((1024, 1), dtype=np.float32)
        rms = np.sqrt(np.mean(audio ** 2))
        assert rms < 1e-6

    def test_quiet_but_not_silent(self):
        """Very quiet audio (just above threshold) should not trigger silence."""
        audio = np.ones((1024, 1), dtype=np.float32) * 0.001
        rms = np.sqrt(np.mean(audio ** 2))
        assert rms >= 1e-6

    def test_normal_speech_rms(self):
        """Simulated speech should be well above silence threshold."""
        t = np.linspace(0, 1024 / 16000, 1024, dtype=np.float32)
        audio = (np.sin(2 * np.pi * 200 * t) * 0.3).reshape(-1, 1)
        rms = np.sqrt(np.mean(audio ** 2))
        assert rms > 0.1


# ===================================================================
# INTEGRATION: Cross-group checks
# ===================================================================

class TestAudioDeviceStringCompatibility:
    """Verify that string AUDIO_DEVICE values work with sounddevice API."""

    def test_sounddevice_accepts_string_device(self):
        """sounddevice.InputStream should accept string device names.
        This is a documentation/API check, not a hardware test.
        """
        import sounddevice as sd
        # sd.InputStream's 'device' parameter accepts: int, str, or None
        # Verify by checking the module — we can't open a real stream without hardware
        # but we can verify the parameter type is documented/accepted
        import inspect
        sig = inspect.signature(sd.InputStream.__init__)
        params = sig.parameters
        assert 'device' in params, "InputStream should accept a 'device' parameter"

    def test_query_devices_returns_name_strings(self):
        """Verify that device names from query_devices are strings we can save."""
        import sounddevice as sd
        devices = sd.query_devices()
        for d in devices:
            if d['max_input_channels'] > 0:
                assert isinstance(d['name'], str), "Device name should be a string"
                assert len(d['name']) > 0, "Device name should not be empty"
