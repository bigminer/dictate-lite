"""Unit tests for transcription_io helpers."""

import numpy as np

import transcription_io


class _Segment:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """Fake model that records what it receives for assertion."""

    def __init__(self):
        self.last_audio = None

    def transcribe(self, audio, **kwargs):
        self.last_audio = audio
        return [_Segment('check'), _Segment('1 2 3')], {'language': 'en'}


def test_transcribe_audio_array_passes_numpy_directly():
    model = _FakeModel()
    audio = np.zeros((160, 1), dtype=np.float32)

    text = transcription_io.transcribe_audio_array(
        model,
        audio,
        beam_size=3,
        language='en',
    )

    assert text == 'check 1 2 3'
    # Model should receive a flattened 1-D float32 array
    assert model.last_audio is not None
    assert model.last_audio.ndim == 1
    assert model.last_audio.dtype == np.float32
    assert model.last_audio.shape[0] == 160


def test_sanitize_transcript_text_removes_control_chars_and_normalizes_whitespace():
    raw = 'hello\tworld\nnext\u200bline\r\n'
    cleaned = transcription_io.sanitize_transcript_text(raw)
    assert cleaned == 'hello world next line'


def test_sanitize_transcript_text_handles_empty_values():
    assert transcription_io.sanitize_transcript_text('') == ''
    assert transcription_io.sanitize_transcript_text(None) == ''


def test_sanitize_transcript_text_ascii_fast_path_strips_controls():
    raw = 'hello\x00 world\x7f\tok'
    cleaned = transcription_io.sanitize_transcript_text(raw)
    assert cleaned == 'hello world ok'
