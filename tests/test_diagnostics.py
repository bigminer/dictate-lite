"""
Tests for src/diagnostics.py — log parsing, report generation, and output.
"""

import json
import os
import sys
import textwrap

import pytest

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import diagnostics


# ---------------------------------------------------------------------------
# Fixtures: synthetic log data
# ---------------------------------------------------------------------------

OLD_FORMAT_LOG = textwrap.dedent("""\
    2026-01-07 16:04:44,472 [INFO] Starting main()
    2026-01-07 16:04:47,860 [DEBUG] Starting new HTTPS connection
    2026-01-07 16:04:49,183 [INFO] Model loaded successfully
    2026-01-07 18:30:10,488 [INFO] Starting main()
    2026-01-07 18:30:13,172 [ERROR] Fatal error in main: Error opening InputStream
    Traceback (most recent call last):
      File "dictate.py", line 233, in main
        stream = sd.InputStream()
    sounddevice.PortAudioError: Error opening InputStream
""")

NEW_FORMAT_LOG = textwrap.dedent("""\
    2026-02-22 11:34:24,940 [INFO] [25572/73cd20e62eac] Starting main()
    2026-02-22 11:34:28,100 [INFO] [25572/73cd20e62eac] Model loaded successfully
    2026-02-22 11:35:00,000 [INFO] [25572/73cd20e62eac] Transcribed (1437ms), 136 chars
    2026-02-22 11:36:00,000 [INFO] [25572/73cd20e62eac] Transcribed (523ms), 42 chars
    2026-02-22 11:37:00,000 [INFO] [25572/73cd20e62eac] No speech detected
    2026-02-22 11:38:00,000 [INFO] [25572/73cd20e62eac] Audio too quiet (RMS=0.0003 < 0.007), skipping
    2026-02-22 11:39:00,000 [INFO] [25572/73cd20e62eac] Audio too short (0.05s < 0.1s), skipping transcription
    2026-02-22 11:40:00,000 [INFO] [25572/73cd20e62eac] No audio captured
    2026-02-22 11:41:00,000 [ERROR] [25572/73cd20e62eac] Transcription error: model failed
    2026-02-22 11:42:00,000 [INFO] [25572/73cd20e62eac] Transcript normalized before output
    2026-02-22 11:43:00,000 [WARNING] [25572/73cd20e62eac] Transcript length 6000 exceeds MAX_TYPED_CHARS=5000; truncating output
    2026-02-22 12:00:00,000 [INFO] [25572/73cd20e62eac] Exit requested from tray menu
    2026-02-22 12:00:01,000 [INFO] [25572/73cd20e62eac] cleanup_resources() called
""")

DEVICE_HEALTH_LOG = textwrap.dedent("""\
    2026-02-20 09:00:00,000 [INFO] Starting main()
    2026-02-20 09:01:00,000 [INFO] Detected input device topology change. Re-resolving preferred microphone.
    2026-02-20 09:02:00,000 [INFO] Switching audio device to: [3] USB Headset
    2026-02-20 09:03:00,000 [ERROR] Failed to switch audio device: Invalid sample rate
    2026-02-20 09:04:00,000 [ERROR] Audio stream appears dead (no callback for 5.2s). Attempting recovery...
    2026-02-20 09:04:05,000 [INFO] Audio stream recovered successfully (device topology change) on open_arg=3
    2026-02-20 09:05:00,000 [WARNING] Recording timeout after 60s - force stopping
    2026-02-20 10:00:00,000 [INFO] Exit requested from tray menu
""")

MULTI_SESSION_LOG = textwrap.dedent("""\
    2026-02-01 08:00:00,000 [INFO] Starting main()
    2026-02-01 08:05:00,000 [INFO] Transcribed (0.8s): hello world
    2026-02-01 12:00:00,000 [INFO] Exit requested from tray menu
    2026-02-01 12:00:01,000 [INFO] cleanup_resources() called
    2026-02-01 12:00:30,000 [INFO] Starting main()
    2026-02-01 12:01:00,000 [INFO] Transcribed (1.2s): quick restart
    2026-02-01 16:00:00,000 [ERROR] Fatal error in main: something broke
    2026-02-02 08:00:00,000 [INFO] Starting main()
    2026-02-02 08:10:00,000 [INFO] Transcribed (0.5s): next day
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entries_from_string(text):
    """Parse log entries from a string by writing to a temp file."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
        f.write(text)
        path = f.name
    try:
        return list(diagnostics.parse_log_file(path))
    finally:
        os.unlink(path)


def _report_from_string(text):
    """Build a finalized DiagnosticReport from a log string."""
    entries = _entries_from_string(text)
    report = diagnostics.DiagnosticReport()
    for entry in entries:
        report.process_entry(entry)
    report.finalize()
    return report


# ---------------------------------------------------------------------------
# Tests: Log line parsing
# ---------------------------------------------------------------------------

class TestLogLineParsing:

    def test_old_format_parsed(self):
        entries = _entries_from_string(OLD_FORMAT_LOG)
        first = entries[0]
        assert first.level == 'INFO'
        assert first.pid is None
        assert first.session_id is None
        assert first.message == 'Starting main()'
        assert first.timestamp.year == 2026

    def test_new_format_parsed(self):
        entries = _entries_from_string(NEW_FORMAT_LOG)
        first = entries[0]
        assert first.level == 'INFO'
        assert first.pid == '25572'
        assert first.session_id == '73cd20e62eac'
        assert first.message == 'Starting main()'

    def test_traceback_continuation(self):
        entries = _entries_from_string(OLD_FORMAT_LOG)
        # The Fatal error entry should have traceback appended
        error_entries = [e for e in entries if e.level == 'ERROR']
        assert len(error_entries) == 1
        assert 'Traceback' in error_entries[0].message
        assert 'PortAudioError' in error_entries[0].message


# ---------------------------------------------------------------------------
# Tests: Transcription timing extraction
# ---------------------------------------------------------------------------

class TestTranscriptionParsing:

    def test_old_format_seconds(self):
        report = _report_from_string(
            '2026-01-23 07:50:13,419 [INFO] Starting main()\n'
            '2026-01-23 07:50:13,419 [INFO] Transcribed (1.6s): Some text here\n'
        )
        assert report.transcription_count == 1
        assert len(report.transcription_times_ms) == 1
        assert report.transcription_times_ms[0] == pytest.approx(1600.0)

    def test_new_format_ms(self):
        report = _report_from_string(NEW_FORMAT_LOG)
        assert report.transcription_count == 2
        assert 1437.0 in report.transcription_times_ms
        assert 523.0 in report.transcription_times_ms

    def test_char_count_extracted(self):
        report = _report_from_string(NEW_FORMAT_LOG)
        assert 136 in report.transcription_chars
        assert 42 in report.transcription_chars

    def test_old_format_no_char_count(self):
        """Old format with text content doesn't have a char count field."""
        report = _report_from_string(
            '2026-01-23 07:50:13,419 [INFO] Starting main()\n'
            '2026-01-23 07:50:13,419 [INFO] Transcribed (1.6s): Some text here\n'
        )
        assert len(report.transcription_chars) == 0


# ---------------------------------------------------------------------------
# Tests: Silent failure counting
# ---------------------------------------------------------------------------

class TestSilentFailures:

    def test_all_types_counted(self):
        report = _report_from_string(NEW_FORMAT_LOG)
        assert report.no_speech_count == 1
        assert report.too_quiet_count == 1
        assert report.too_short_count == 1
        assert report.no_audio_count == 1
        assert report.transcription_error_count == 1
        assert report.normalization_count == 1
        assert report.truncation_count == 1

    def test_failure_rate(self):
        report = _report_from_string(NEW_FORMAT_LOG)
        # 2 successful transcriptions, 4 silent failures (no_speech, too_quiet, too_short, no_audio)
        # + 1 transcription_error = 5 failures
        # Rate = 5 / (2 + 5) = 71.4%
        assert report._failure_rate_pct() == pytest.approx(71.4, abs=0.1)


# ---------------------------------------------------------------------------
# Tests: Session tracking
# ---------------------------------------------------------------------------

class TestSessionTracking:

    def test_session_boundaries(self):
        report = _report_from_string(MULTI_SESSION_LOG)
        assert len(report.sessions) == 3

    def test_clean_shutdown_detected(self):
        report = _report_from_string(MULTI_SESSION_LOG)
        assert report.sessions[0].end_reason == 'clean'

    def test_fatal_error_detected(self):
        report = _report_from_string(MULTI_SESSION_LOG)
        assert report.sessions[1].end_reason == 'fatal'

    def test_active_session_detected(self):
        """The last session has no shutdown marker — should be marked 'active'."""
        report = _report_from_string(MULTI_SESSION_LOG)
        assert report.sessions[2].end_reason == 'active'

    def test_session_duration(self):
        report = _report_from_string(MULTI_SESSION_LOG)
        first = report.sessions[0]
        assert first.duration_s() == pytest.approx(4 * 3600 + 1, abs=1)

    def test_rapid_restart_detected(self):
        data = _report_from_string(MULTI_SESSION_LOG).to_dict()
        # Session 1 ends at 12:00:01, Session 2 starts at 12:00:30 (29s gap)
        assert data['session_health']['rapid_restarts'] == 1

    def test_inferred_crash_from_no_shutdown(self):
        """When a new session starts without the previous having a shutdown marker."""
        log = textwrap.dedent("""\
            2026-03-01 08:00:00,000 [INFO] Starting main()
            2026-03-01 10:00:00,000 [INFO] Starting main()
            2026-03-01 12:00:00,000 [INFO] Exit requested from tray menu
        """)
        report = _report_from_string(log)
        assert report.sessions[0].end_reason == 'inferred'
        assert report.sessions[1].end_reason == 'clean'


# ---------------------------------------------------------------------------
# Tests: Error grouping
# ---------------------------------------------------------------------------

class TestErrorGrouping:

    def test_duplicate_errors_grouped(self):
        log = textwrap.dedent("""\
            2026-02-06 12:13:16,032 [ERROR] Failed to switch audio device: Invalid sample rate
            2026-02-06 12:13:58,629 [ERROR] Failed to switch audio device: Invalid sample rate
            2026-02-06 12:15:08,580 [ERROR] Failed to switch audio device: Invalid sample rate
        """)
        report = _report_from_string(log)
        assert len(report.errors) == 1
        key = list(report.errors.keys())[0]
        assert report.errors[key]['count'] == 3

    def test_error_with_traceback_uses_first_line_as_key(self):
        entries = _entries_from_string(OLD_FORMAT_LOG)
        report = diagnostics.DiagnosticReport()
        for entry in entries:
            report.process_entry(entry)
        report.finalize()
        # The error key should be the first line, not include traceback
        for key in report.errors:
            assert '\n' not in key


# ---------------------------------------------------------------------------
# Tests: Device health
# ---------------------------------------------------------------------------

class TestDeviceHealth:

    def test_device_events_counted(self):
        report = _report_from_string(DEVICE_HEALTH_LOG)
        assert report.topology_changes == 1
        assert report.device_switch_attempts == 1
        assert report.device_switch_failures == 1
        assert report.stream_deaths == 1
        assert report.stream_recoveries == 1
        assert report.recording_timeouts == 1


# ---------------------------------------------------------------------------
# Tests: Percentile calculation
# ---------------------------------------------------------------------------

class TestPercentile:

    def test_single_value(self):
        assert diagnostics._percentile([100.0], 50) == 100.0

    def test_two_values_median(self):
        assert diagnostics._percentile([100.0, 200.0], 50) == 150.0

    def test_p95_with_many_values(self):
        vals = list(range(1, 101))  # 1..100
        p95 = diagnostics._percentile(vals, 95)
        assert p95 == pytest.approx(95.05, abs=0.1)

    def test_empty_list(self):
        assert diagnostics._percentile([], 50) == 0.0


# ---------------------------------------------------------------------------
# Tests: JSON output
# ---------------------------------------------------------------------------

class TestJsonOutput:

    def test_to_dict_serializable(self):
        report = _report_from_string(NEW_FORMAT_LOG)
        data = report.to_dict()
        # Should not raise
        json_str = json.dumps(data, default=str)
        parsed = json.loads(json_str)
        assert 'errors' in parsed
        assert 'silent_failures' in parsed
        assert 'transcription_performance' in parsed
        assert 'session_health' in parsed
        assert 'device_health' in parsed

    def test_empty_report_serializable(self):
        report = diagnostics.DiagnosticReport()
        report.finalize()
        data = report.to_dict()
        json_str = json.dumps(data, default=str)
        parsed = json.loads(json_str)
        assert parsed['total_log_entries'] == 0


# ---------------------------------------------------------------------------
# Tests: Last session filtering
# ---------------------------------------------------------------------------

class TestLastSessionFilter:

    def test_last_session_only(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
            f.write(MULTI_SESSION_LOG)
            path = f.name
        try:
            entries = list(diagnostics.parse_all_logs([path], last_session_only=True))
            # Should start from the last "Starting main()" (2026-02-02 08:00)
            assert entries[0].message == 'Starting main()'
            assert entries[0].timestamp.day == 2
            assert entries[0].timestamp.month == 2
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tests: Helper formatting
# ---------------------------------------------------------------------------

class TestFormatting:

    def test_format_ms_under_1s(self):
        assert diagnostics._format_ms(450) == '450ms'

    def test_format_ms_over_1s(self):
        assert diagnostics._format_ms(1500) == '1.5s'

    def test_format_duration_seconds(self):
        assert diagnostics._format_duration(45) == '45s'

    def test_format_duration_minutes(self):
        assert diagnostics._format_duration(300) == '5m'

    def test_format_duration_hours(self):
        assert diagnostics._format_duration(7500) == '2h 5m'

    def test_format_bytes_kb(self):
        assert diagnostics._format_bytes(2048) == '2.0 KB'

    def test_format_bytes_mb(self):
        assert diagnostics._format_bytes(1572820) == '1.5 MB'


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_no_log_files(self):
        """discover_log_files with a nonexistent dir returns empty list."""
        files = diagnostics.discover_log_files('/nonexistent/path')
        assert files == []

    def test_empty_log_content(self):
        """Parsing an empty file yields no entries."""
        entries = _entries_from_string('')
        assert entries == []

    def test_report_with_no_entries(self):
        """Finalized empty report should not crash."""
        report = diagnostics.DiagnosticReport()
        report.finalize()
        data = report.to_dict()
        assert data['total_log_entries'] == 0
        assert data['session_health']['total_sessions'] == 0
