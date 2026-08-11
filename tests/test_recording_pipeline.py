"""Tests for voice_dictation.recording_pipeline helpers."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import numpy as np

from voice_dictation import recording_pipeline


def _run_transcribe(audio_seconds=2.0, text='hello there', **overrides):
    audio_data = np.zeros(int(16000 * audio_seconds), dtype=np.float32)
    io_module = MagicMock()
    io_module.transcribe_audio_array.return_value = text
    io_module.sanitize_transcript_text.return_value = text
    logger = MagicMock()

    kwargs = dict(
        model=MagicMock(),
        transcription_io_module=io_module,
        sample_rate=16000,
        transcribe_language='en',
        vocabulary='',
        max_typed_chars=1000,
        logger=logger,
    )
    kwargs.update(overrides)
    result = recording_pipeline.transcribe_audio(audio_data, **kwargs)
    return result, io_module, logger


def test_transcribe_audio_logs_duration_and_realtime_factor():
    _, _, logger = _run_transcribe(audio_seconds=2.0, text='hello there')

    completion_messages = [
        call.args[0] for call in logger.info.call_args_list
        if 'Transcription complete' in call.args[0]
    ]
    assert len(completion_messages) == 1
    assert re.search(
        r'Transcription complete: \d+ms for 2\.0s audio \(rtf=\d+\.\d\d\), 11 chars',
        completion_messages[0],
    )
