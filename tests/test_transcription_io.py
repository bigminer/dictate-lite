"""Unit tests for transcription_io helpers."""

import os

import numpy as np

import transcription_io


class _Segment:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    def transcribe(self, path, **kwargs):
        assert os.path.exists(path)
        return [_Segment('check'), _Segment('1 2 3')], {'language': 'en'}


class _FakeSoundFile:
    def __init__(self):
        self.writes = []

    def write(self, path, audio, sample_rate):
        self.writes.append((path, sample_rate, len(audio)))
        with open(path, 'wb') as handle:
            handle.write(b'RIFF')


def test_transcribe_audio_array_uses_temp_file_and_cleans_up():
    fake_sf = _FakeSoundFile()
    model = _FakeModel()
    audio = np.zeros((160, 1), dtype=np.float32)

    text = transcription_io.transcribe_audio_array(
        model,
        audio,
        sample_rate=16000,
        sf_module=fake_sf,
        beam_size=3,
        language='en',
    )

    assert text == 'check 1 2 3'
    assert len(fake_sf.writes) == 1
    temp_path = fake_sf.writes[0][0]
    assert not os.path.exists(temp_path)


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
