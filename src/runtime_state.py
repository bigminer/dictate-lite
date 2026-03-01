"""
Runtime state persistence helpers for Voice Dictation lifecycle markers.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from config_store import atomic_write_text

APP_DIR_NAME = 'VoiceDictation'
STATE_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), APP_DIR_NAME)
STATE_FILE = os.path.join(STATE_DIR, 'state.json')


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
        atomic_write_text(state_file, payload, encoding='utf-8')
    except Exception as e:
        logger.warning(f"Unable to write runtime state file '{state_file}': {e}")
