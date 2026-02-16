"""Unit tests for DictationAppState defaults."""

from app_state import DictationAppState


def test_state_defaults():
    state = DictationAppState()
    assert state.active_mic_name is None
    assert state.active_mic_index is None
    assert state.active_mic_hostapi is None
    assert state.audio_stream is None
    assert state.is_recording is False
    assert state.recorded_frames == []
    assert state.shutdown_event.is_set() is False
