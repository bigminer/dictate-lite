"""Unit tests for config_store helpers."""

from pathlib import Path

import config_store


def test_format_python_literal_variants():
    assert config_store.format_python_literal(None) == 'None'
    assert config_store.format_python_literal(True) == 'True'
    assert config_store.format_python_literal(False) == 'False'
    assert config_store.format_python_literal(5) == '5'
    assert config_store.format_python_literal(1.25) == '1.25'
    assert config_store.format_python_literal("Gary's Mic") == "'Gary\\'s Mic'"


def test_upsert_assignment_replaces_existing():
    text = "AUDIO_DEVICE = None\nHOTKEY = 'alt+f'\n"
    updated = config_store.upsert_assignment(text, 'AUDIO_DEVICE', "'USB Mic'")
    assert "AUDIO_DEVICE = 'USB Mic'" in updated
    assert 'AUDIO_DEVICE = None' not in updated


def test_update_config_values_appends_missing_key(tmp_path):
    config_file = tmp_path / 'config.py'
    config_file.write_text("HOTKEY = 'alt+f'\n", encoding='utf-8')

    ok = config_store.update_config_values(
        str(config_file),
        updates={'AUDIO_DEVICE_UID': 'abc123'},
        comments={'AUDIO_DEVICE_UID': '# Saved audio device identity (auto-managed)'},
    )

    assert ok is True
    content = config_file.read_text(encoding='utf-8')
    assert '# Saved audio device identity (auto-managed)' in content
    assert "AUDIO_DEVICE_UID = 'abc123'" in content
