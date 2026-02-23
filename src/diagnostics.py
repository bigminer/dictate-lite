"""
Voice Dictation diagnostic log analyzer.

Parses rotating log files and state.json to surface errors, performance
stats, silent failures, session health, and device issues that are
invisible to the user during normal operation.

Run with:
    python src/diagnostics.py               # console report
    python src/diagnostics.py --json        # machine-readable JSON
    python src/diagnostics.py --last-session # most recent session only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import statistics
from collections import defaultdict, namedtuple
from datetime import datetime

# ---------------------------------------------------------------------------
# Ensure src/ is on sys.path so we can import sibling modules
# ---------------------------------------------------------------------------
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import runtime_state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = os.path.join(os.path.expanduser('~'), 'voice-dictation')
LOG_BASENAME = 'dictation.log'
MAX_BACKUPS = 5

# ---------------------------------------------------------------------------
# Log-line parsing
# ---------------------------------------------------------------------------

LogEntry = namedtuple('LogEntry', ['timestamp', 'level', 'pid', 'session_id', 'message'])

# Matches both old format (no pid/session) and new format (with pid/session)
LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})'  # timestamp
    r' \[(\w+)\]'                                        # level
    r'(?: \[(\d+)/([a-f0-9]+)\])?'                      # optional [pid/session_id]
    r' (.*)$'                                            # message
)

TIMESTAMP_FMT = '%Y-%m-%d %H:%M:%S,%f'

# Transcription timing
TRANSCRIBED_SECONDS_RE = re.compile(r'Transcribed \((\d+\.\d+)s\)')
TRANSCRIBED_MS_RE = re.compile(r'Transcribed \((\d+)ms\)')
TRANSCRIBED_CHARS_RE = re.compile(r',\s*(\d+) chars')

# Silent failures
NO_SPEECH_MSG = 'No speech detected'
NO_AUDIO_MSG = 'No audio captured'
TOO_SHORT_RE = re.compile(r'^Audio too short \(')
TOO_QUIET_RE = re.compile(r'^Audio too quiet \(')
TRANSCRIPTION_ERROR_RE = re.compile(r'^Transcription error: (.+)$')
TRUNCATION_RE = re.compile(r'^Transcript length (\d+) exceeds MAX_TYPED_CHARS=(\d+)')
NORMALIZED_MSG = 'Transcript normalized before output'

# Session boundaries
SESSION_START_MSG = 'Starting main()'
CLEAN_SHUTDOWN_RE = re.compile(r'^cleanup_resources\(\) called')
EXIT_REQUEST_MSG = 'Exit requested from tray menu'
RESTART_REQUEST_MSG = 'Restart requested from tray menu'
FATAL_ERROR_RE = re.compile(r'^Fatal error in main: (.+)$')

# Device/stream health
STREAM_DEAD_RE = re.compile(r'^Audio stream appears dead')
STREAM_RECOVERED_RE = re.compile(r'^Audio stream recovered successfully')
TOPOLOGY_CHANGE_MSG = 'Detected input device topology change. Re-resolving preferred microphone.'
DEVICE_SWITCH_RE = re.compile(r'^Switching audio device to:')
DEVICE_SWITCH_FAIL_RE = re.compile(r'^Failed to switch audio device:')
RECORDING_TIMEOUT_RE = re.compile(r'^Recording timeout after')


# ---------------------------------------------------------------------------
# Log file discovery
# ---------------------------------------------------------------------------

def discover_log_files(log_dir=LOG_DIR):
    """Find all dictation log files in chronological order (oldest first)."""
    files = []
    # Rotated backups: .5 (oldest) through .1 (newest rotated)
    for i in range(MAX_BACKUPS, 0, -1):
        path = os.path.join(log_dir, f'{LOG_BASENAME}.{i}')
        if os.path.isfile(path):
            files.append(path)
    # Current (newest)
    base = os.path.join(log_dir, LOG_BASENAME)
    if os.path.isfile(base):
        files.append(base)
    return files


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def parse_log_file(path):
    """Parse a single log file, yielding LogEntry objects.

    Multi-line entries (tracebacks) are concatenated into the previous
    entry's message.
    """
    pending = None
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            line = raw_line.rstrip('\n\r')
            m = LOG_LINE_RE.match(line)
            if m:
                if pending is not None:
                    yield pending
                ts_str, level, pid, session_id, message = m.groups()
                try:
                    ts = datetime.strptime(ts_str, TIMESTAMP_FMT)
                except ValueError:
                    ts = None
                pending = LogEntry(ts, level, pid, session_id, message)
            elif pending is not None:
                # Continuation line (traceback, etc.) — append to message
                pending = pending._replace(message=pending.message + '\n' + line)
    if pending is not None:
        yield pending


def parse_all_logs(log_files, last_session_only=False):
    """Parse all log files and yield entries, optionally filtered to last session."""
    if last_session_only:
        # Collect all, then slice from the last session start
        all_entries = []
        for path in log_files:
            all_entries.extend(parse_log_file(path))
        last_start_idx = None
        for i, entry in enumerate(all_entries):
            if entry.message == SESSION_START_MSG:
                last_start_idx = i
        if last_start_idx is not None:
            yield from all_entries[last_start_idx:]
        else:
            yield from all_entries
    else:
        for path in log_files:
            yield from parse_log_file(path)


# ---------------------------------------------------------------------------
# Diagnostic report
# ---------------------------------------------------------------------------

class _Session:
    """Tracks a single application session from start to end."""
    __slots__ = ('start_time', 'end_time', 'end_reason')

    def __init__(self, start_time):
        self.start_time = start_time
        self.end_time = None
        self.end_reason = None  # 'clean', 'fatal', 'inferred'

    def duration_s(self):
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


class DiagnosticReport:
    """Accumulates findings from log entries and produces a report."""

    def __init__(self):
        # Errors & warnings
        self.errors = defaultdict(lambda: {'count': 0, 'last_seen': None})
        self.warnings = defaultdict(lambda: {'count': 0, 'last_seen': None})

        # Transcription performance
        self.transcription_times_ms = []
        self.transcription_chars = []

        # Silent failures
        self.no_speech_count = 0
        self.no_audio_count = 0
        self.too_short_count = 0
        self.too_quiet_count = 0
        self.transcription_error_count = 0
        self.truncation_count = 0
        self.normalization_count = 0

        # Session tracking
        self.sessions = []
        self._current_session = None

        # Device health
        self.topology_changes = 0
        self.stream_deaths = 0
        self.stream_recoveries = 0
        self.device_switch_attempts = 0
        self.device_switch_failures = 0
        self.recording_timeouts = 0

        # Metadata
        self.total_entries = 0
        self.first_timestamp = None
        self.last_timestamp = None
        self.transcription_count = 0

    def process_entry(self, entry: LogEntry):
        """Route a single log entry to the appropriate collector."""
        self.total_entries += 1
        if entry.timestamp:
            if self.first_timestamp is None:
                self.first_timestamp = entry.timestamp
            self.last_timestamp = entry.timestamp

        msg = entry.message

        # --- Session boundaries ---
        if msg == SESSION_START_MSG:
            self._close_current_session(entry.timestamp, 'inferred')
            self._current_session = _Session(entry.timestamp)

        elif CLEAN_SHUTDOWN_RE.match(msg) or msg == EXIT_REQUEST_MSG or msg == RESTART_REQUEST_MSG:
            self._close_current_session(entry.timestamp, 'clean')

        elif FATAL_ERROR_RE.match(msg):
            self._close_current_session(entry.timestamp, 'fatal')

        # --- Errors ---
        if entry.level == 'ERROR':
            # Truncate message for grouping (first line only)
            key = msg.split('\n', 1)[0]
            self.errors[key]['count'] += 1
            self.errors[key]['last_seen'] = entry.timestamp

        # --- Warnings (only actionable ones) ---
        if entry.level == 'WARNING':
            key = msg.split('\n', 1)[0]
            self.warnings[key]['count'] += 1
            self.warnings[key]['last_seen'] = entry.timestamp

        # --- Transcription timing ---
        m = TRANSCRIBED_SECONDS_RE.search(msg)
        if m:
            self.transcription_count += 1
            self.transcription_times_ms.append(float(m.group(1)) * 1000)
            mc = TRANSCRIBED_CHARS_RE.search(msg)
            if mc:
                self.transcription_chars.append(int(mc.group(1)))
            return

        m = TRANSCRIBED_MS_RE.search(msg)
        if m:
            self.transcription_count += 1
            self.transcription_times_ms.append(float(m.group(1)))
            mc = TRANSCRIBED_CHARS_RE.search(msg)
            if mc:
                self.transcription_chars.append(int(mc.group(1)))
            return

        # --- Silent failures ---
        if msg == NO_SPEECH_MSG:
            self.no_speech_count += 1
        elif msg == NO_AUDIO_MSG:
            self.no_audio_count += 1
        elif TOO_SHORT_RE.match(msg):
            self.too_short_count += 1
        elif TOO_QUIET_RE.match(msg):
            self.too_quiet_count += 1
        elif TRANSCRIPTION_ERROR_RE.match(msg):
            self.transcription_error_count += 1
        elif TRUNCATION_RE.match(msg):
            self.truncation_count += 1
        elif msg == NORMALIZED_MSG:
            self.normalization_count += 1

        # --- Device health ---
        elif msg == TOPOLOGY_CHANGE_MSG:
            self.topology_changes += 1
        elif STREAM_DEAD_RE.match(msg):
            self.stream_deaths += 1
        elif STREAM_RECOVERED_RE.match(msg):
            self.stream_recoveries += 1
        elif DEVICE_SWITCH_RE.match(msg):
            self.device_switch_attempts += 1
        elif DEVICE_SWITCH_FAIL_RE.match(msg):
            self.device_switch_failures += 1
        elif RECORDING_TIMEOUT_RE.match(msg):
            self.recording_timeouts += 1

    def _close_current_session(self, end_time, reason):
        """Close the current session and add it to the list."""
        if self._current_session is not None:
            self._current_session.end_time = end_time
            self._current_session.end_reason = reason
            self.sessions.append(self._current_session)
            self._current_session = None

    def finalize(self):
        """Finalize report after all entries have been processed."""
        # Close any still-open session
        if self._current_session is not None:
            self._current_session.end_time = self.last_timestamp
            self._current_session.end_reason = 'active'
            self.sessions.append(self._current_session)
            self._current_session = None

    def _silent_failure_total(self):
        return (self.no_speech_count + self.no_audio_count +
                self.too_short_count + self.too_quiet_count +
                self.transcription_error_count)

    def _failure_rate_pct(self):
        total_attempts = self.transcription_count + self._silent_failure_total()
        if total_attempts == 0:
            return 0.0
        return (self._silent_failure_total() / total_attempts) * 100

    def to_dict(self):
        """Return a JSON-serializable dict of the full report."""
        def _ts(dt):
            return dt.isoformat() if dt else None

        # Transcription stats
        perf = {}
        if self.transcription_times_ms:
            vals = sorted(self.transcription_times_ms)
            perf = {
                'count': self.transcription_count,
                'latency_ms': {
                    'min': round(min(vals), 1),
                    'avg': round(statistics.mean(vals), 1),
                    'p50': round(_percentile(vals, 50), 1),
                    'p95': round(_percentile(vals, 95), 1),
                    'max': round(max(vals), 1),
                },
            }
            if self.transcription_chars:
                perf['chars'] = {
                    'min': min(self.transcription_chars),
                    'avg': round(statistics.mean(self.transcription_chars), 1),
                    'max': max(self.transcription_chars),
                }
        else:
            perf = {'count': 0}

        # Session stats
        clean = sum(1 for s in self.sessions if s.end_reason == 'clean')
        fatal = sum(1 for s in self.sessions if s.end_reason == 'fatal')
        inferred = sum(1 for s in self.sessions if s.end_reason == 'inferred')
        durations = [s.duration_s() for s in self.sessions if s.duration_s() is not None and s.duration_s() > 0]

        # Rapid restarts (sessions starting within 60s of previous end)
        rapid_restarts = 0
        for i in range(1, len(self.sessions)):
            prev_end = self.sessions[i - 1].end_time
            curr_start = self.sessions[i].start_time
            if prev_end and curr_start:
                gap = (curr_start - prev_end).total_seconds()
                if gap < 60:
                    rapid_restarts += 1

        return {
            'generated_at': datetime.now().isoformat(timespec='seconds'),
            'time_span': {
                'start': _ts(self.first_timestamp),
                'end': _ts(self.last_timestamp),
            },
            'total_log_entries': self.total_entries,
            'errors': [
                {'message': msg, 'count': info['count'], 'last_seen': _ts(info['last_seen'])}
                for msg, info in sorted(self.errors.items(), key=lambda x: -x[1]['count'])
            ],
            'warnings': [
                {'message': msg, 'count': info['count'], 'last_seen': _ts(info['last_seen'])}
                for msg, info in sorted(self.warnings.items(), key=lambda x: -x[1]['count'])
            ],
            'transcription_performance': perf,
            'silent_failures': {
                'total': self._silent_failure_total(),
                'no_speech': self.no_speech_count,
                'no_audio': self.no_audio_count,
                'too_short': self.too_short_count,
                'too_quiet': self.too_quiet_count,
                'transcription_errors': self.transcription_error_count,
                'truncations': self.truncation_count,
                'normalizations': self.normalization_count,
                'failure_rate_pct': round(self._failure_rate_pct(), 1),
            },
            'session_health': {
                'total_sessions': len(self.sessions),
                'clean_shutdowns': clean,
                'fatal_errors': fatal,
                'inferred_crashes': inferred,
                'median_duration_s': round(statistics.median(durations), 1) if durations else None,
                'rapid_restarts': rapid_restarts,
            },
            'device_health': {
                'topology_changes': self.topology_changes,
                'stream_deaths': self.stream_deaths,
                'stream_recoveries': self.stream_recoveries,
                'switch_attempts': self.device_switch_attempts,
                'switch_failures': self.device_switch_failures,
                'recording_timeouts': self.recording_timeouts,
            },
        }

    def print_console(self, state_data=None, log_files=None):
        """Print human-readable diagnostic report to console."""
        data = self.to_dict()

        print()
        print('=' * 52)
        print('  Voice Dictation - Diagnostic Report')
        print('=' * 52)
        print(f'  Generated: {data["generated_at"]}')
        if log_files:
            total_bytes = sum(os.path.getsize(f) for f in log_files)
            print(f'  Log files analyzed: {len(log_files)} ({_format_bytes(total_bytes)})')
        if data['time_span']['start'] and data['time_span']['end']:
            print(f'  Time span: {data["time_span"]["start"][:10]} to {data["time_span"]["end"][:10]}')
        print(f'  Total log entries: {data["total_log_entries"]:,}')
        print()

        # --- Current State ---
        if state_data:
            print('--- Current State ---')
            status = state_data.get('status', 'unknown')
            updated = state_data.get('updated_at', '?')
            pid = state_data.get('pid', '?')
            print(f'  [INFO]  Status: {status} (updated {updated})')
            print(f'  [INFO]  PID: {pid}')
            details = state_data.get('details')
            if isinstance(details, dict):
                utterances = details.get('utterance_count', 0)
                chars = details.get('total_chars_typed', 0)
                errors = details.get('transcription_errors', 0)
                fallbacks = details.get('device_fallback_count', 0)
                parts = [f'{utterances} utterances', f'{chars} chars typed']
                if errors:
                    parts.append(f'{errors} transcription errors')
                if fallbacks:
                    parts.append(f'{fallbacks} device fallbacks')
                print(f'  [INFO]  Session metrics: {", ".join(parts)}')
            reason = state_data.get('reason')
            if reason:
                print(f'  [INFO]  Reason: {reason}')
            print()

        # --- Errors ---
        errors = data['errors']
        if errors:
            total_err = sum(e['count'] for e in errors)
            print(f'--- Errors ({len(errors)} unique, {total_err} total) ---')
            for e in errors[:10]:
                last = e['last_seen'][:10] if e['last_seen'] else '?'
                # Truncate long messages for display
                msg = e['message'][:100]
                print(f'  [ISSUE] {msg} ({e["count"]}x, last: {last})')
            if len(errors) > 10:
                print(f'  ... and {len(errors) - 10} more unique errors')
            print()
        else:
            print('--- Errors ---')
            print('  [OK]    No errors found')
            print()

        # --- Transcription Performance ---
        perf = data['transcription_performance']
        print(f'--- Transcription Performance ({perf.get("count", 0)} transcriptions) ---')
        if 'latency_ms' in perf:
            lat = perf['latency_ms']
            print(f'  [INFO]  Latency: min={_format_ms(lat["min"])}  '
                  f'avg={_format_ms(lat["avg"])}  '
                  f'p50={_format_ms(lat["p50"])}  '
                  f'p95={_format_ms(lat["p95"])}  '
                  f'max={_format_ms(lat["max"])}')
            if 'chars' in perf:
                c = perf['chars']
                print(f'  [INFO]  Output: min={c["min"]} chars  avg={c["avg"]:.0f} chars  max={c["max"]} chars')
        else:
            print('  [INFO]  No transcription data available')
        print()

        # --- Silent Failures ---
        sf = data['silent_failures']
        print(f'--- Silent Failures ({sf["total"]} total) ---')
        if sf['total'] > 0:
            if sf['too_quiet']:
                print(f'  [WARN]  Audio too quiet (noise gate): {sf["too_quiet"]}')
            if sf['no_audio']:
                print(f'  [WARN]  No audio captured: {sf["no_audio"]}')
            if sf['no_speech']:
                print(f'  [WARN]  No speech detected: {sf["no_speech"]}')
            if sf['too_short']:
                print(f'  [WARN]  Audio too short: {sf["too_short"]}')
            if sf['transcription_errors']:
                print(f'  [ISSUE] Transcription errors: {sf["transcription_errors"]}')
            if sf['truncations']:
                print(f'  [WARN]  Truncated transcriptions: {sf["truncations"]}')
            print(f'  [INFO]  Failure rate: {sf["failure_rate_pct"]:.1f}% of recording attempts produced no output')
        else:
            print('  [OK]    No silent failures')
        if sf['normalizations']:
            print(f'  [INFO]  Transcripts normalized: {sf["normalizations"]}')
        print()

        # --- Session Health ---
        sh = data['session_health']
        print(f'--- Session Health ({sh["total_sessions"]} sessions) ---')
        print(f'  [INFO]  Clean shutdowns: {sh["clean_shutdowns"]}  |  '
              f'Fatal errors: {sh["fatal_errors"]}  |  '
              f'Inferred crashes: {sh["inferred_crashes"]}')
        if sh['median_duration_s'] is not None:
            print(f'  [INFO]  Median session duration: {_format_duration(sh["median_duration_s"])}')
        if sh['inferred_crashes'] > 0:
            print(f'  [WARN]  {sh["inferred_crashes"]} session(s) ended without clean shutdown')
        if sh['rapid_restarts'] > 0:
            print(f'  [WARN]  {sh["rapid_restarts"]} rapid restart(s) detected (< 60s between sessions)')
        print()

        # --- Device Health ---
        dh = data['device_health']
        print('--- Device Health ---')
        print(f'  [INFO]  Topology changes: {dh["topology_changes"]}')
        if dh['stream_deaths'] or dh['stream_recoveries']:
            recovery_pct = ''
            if dh['stream_deaths'] > 0:
                rate = (dh['stream_recoveries'] / dh['stream_deaths']) * 100
                recovery_pct = f' ({rate:.0f}% recovery)'
            print(f'  [INFO]  Stream deaths: {dh["stream_deaths"]}  |  '
                  f'Recoveries: {dh["stream_recoveries"]}{recovery_pct}')
        if dh['switch_attempts'] or dh['switch_failures']:
            print(f'  [INFO]  Device switch attempts: {dh["switch_attempts"]}  |  '
                  f'Failures: {dh["switch_failures"]}')
            if dh['switch_failures'] > 0:
                print(f'  [WARN]  {dh["switch_failures"]} device switch failure(s)')
        if dh['recording_timeouts']:
            print(f'  [WARN]  Recording timeouts: {dh["recording_timeouts"]}')
        if not any([dh['topology_changes'], dh['stream_deaths'],
                    dh['switch_attempts'], dh['recording_timeouts']]):
            print('  [OK]    No device issues detected')
        print()

        print('=' * 52)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(sorted_values, pct):
    """Compute the *pct*-th percentile from a pre-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100) * (len(sorted_values) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return sorted_values[low]
    frac = rank - low
    return sorted_values[low] + frac * (sorted_values[high] - sorted_values[low])


def _format_ms(ms):
    """Format milliseconds as human-friendly string."""
    if ms >= 1000:
        return f'{ms / 1000:.1f}s'
    return f'{ms:.0f}ms'


def _format_duration(seconds):
    """Format a duration in seconds as human-friendly string."""
    if seconds < 60:
        return f'{seconds:.0f}s'
    if seconds < 3600:
        return f'{seconds / 60:.0f}m'
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f'{hours}h {mins}m'


def _format_bytes(nbytes):
    """Format byte count as human-friendly string."""
    if nbytes < 1024:
        return f'{nbytes} B'
    if nbytes < 1024 * 1024:
        return f'{nbytes / 1024:.1f} KB'
    return f'{nbytes / (1024 * 1024):.1f} MB'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Voice Dictation diagnostic log analyzer',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Output machine-readable JSON instead of console report',
    )
    parser.add_argument(
        '--last-session', action='store_true',
        help='Limit analysis to the most recent session only',
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Discover log files
    log_files = discover_log_files()
    if not log_files:
        if args.json:
            print(json.dumps({'error': 'No log files found', 'log_dir': LOG_DIR}))
        else:
            print(f'\n  No log files found in {LOG_DIR}')
            print(f'  Expected: {LOG_BASENAME}')
        return 1

    # Read state.json
    state_data = runtime_state.read_runtime_state()

    # Parse and analyze
    report = DiagnosticReport()
    for entry in parse_all_logs(log_files, last_session_only=args.last_session):
        report.process_entry(entry)
    report.finalize()

    # Output
    if args.json:
        output = report.to_dict()
        output['state'] = state_data
        output['log_files'] = log_files
        print(json.dumps(output, indent=2, default=str))
    else:
        report.print_console(state_data=state_data, log_files=log_files)
        try:
            input('  Press Enter to close...')
        except EOFError:
            pass

    return 0


if __name__ == '__main__':
    sys.exit(main())
