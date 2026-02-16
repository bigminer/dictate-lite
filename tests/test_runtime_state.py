"""Unit tests for runtime state persistence helpers."""

import json
from pathlib import Path

import runtime_state


def test_read_runtime_state_missing_file_returns_empty(tmp_path):
    state_file = tmp_path / 'state.json'
    assert runtime_state.read_runtime_state(state_file=state_file) == {}


def test_write_and_read_runtime_state_round_trip(tmp_path):
    state_file = tmp_path / 'state.json'
    runtime_state.write_runtime_state(
        status='ready',
        reason='startup_complete',
        details='ok',
        pid=1234,
        state_file=state_file
    )
    data = runtime_state.read_runtime_state(state_file=state_file)
    assert data['status'] == 'ready'
    assert data['reason'] == 'startup_complete'
    assert data['details'] == 'ok'
    assert data['pid'] == 1234
    assert 'updated_at' in data


def test_read_runtime_state_invalid_json_returns_empty(tmp_path):
    state_file = tmp_path / 'state.json'
    state_file.write_text('{invalid json', encoding='utf-8')
    assert runtime_state.read_runtime_state(state_file=state_file) == {}


def test_write_runtime_state_overwrites_existing_status(tmp_path):
    state_file = tmp_path / 'state.json'
    state_file.write_text(json.dumps({'status': 'starting', 'pid': 1}), encoding='utf-8')
    runtime_state.write_runtime_state(status='audio_error', reason='stream_open_failed', state_file=state_file, pid=2)
    data = runtime_state.read_runtime_state(state_file=state_file)
    assert data['status'] == 'audio_error'
    assert data['reason'] == 'stream_open_failed'
    assert data['pid'] == 2

