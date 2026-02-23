"""
Runtime state persistence helpers for Voice Dictation lifecycle markers.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

APP_DIR_NAME = 'VoiceDictation'
STATE_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), APP_DIR_NAME)
STATE_FILE = os.path.join(STATE_DIR, 'state.json')


def _atomic_write_text(path, content, encoding='utf-8'):
    directory = os.path.dirname(path)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding=encoding,
            delete=False,
            dir=directory,
            prefix='state.',
            suffix='.tmp'
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def read_runtime_state(state_file=STATE_FILE, logger=logging.getLogger(__name__)):
    """Read runtime state JSON. Returns {} when missing or invalid."""
    if not os.path.exists(state_file):
        return {}

    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Unable to read runtime state file '{state_file}': {e}")
    return {}


def write_runtime_state(status, reason=None, details=None, pid=None, state_file=STATE_FILE, logger=logging.getLogger(__name__)):
    """Atomically persist runtime lifecycle state."""
    state = read_runtime_state(state_file=state_file, logger=logger)
    state['status'] = status
    state['updated_at'] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    state['pid'] = os.getpid() if pid is None else int(pid)
    if reason is not None:
        state['reason'] = reason
    if details is not None:
        state['details'] = details

    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        payload = json.dumps(state, indent=2, sort_keys=True) + '\n'
        _atomic_write_text(state_file, payload, encoding='utf-8')
    except Exception as e:
        logger.warning(f"Unable to write runtime state file '{state_file}': {e}")
