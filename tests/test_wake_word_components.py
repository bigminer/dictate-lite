"""Tests for wake word supporting components: file writer, shared buffer, mode toggle."""

import os

import numpy as np

from voice_dictation.transcription_file_writer import append_transcription
from voice_dictation.shared_audio_buffer import SharedAudioBuffer
from voice_dictation.wake_word_mode import WakeWordMode


# ---------------------------------------------------------------------------
# Cycle 1: Append transcription to file
# ---------------------------------------------------------------------------

def test_append_transcription_to_file(tmp_path):
    filepath = tmp_path / 'transcriptions.txt'

    append_transcription(str(filepath), 'Hello world')

    assert filepath.exists()
    lines = filepath.read_text().splitlines()
    assert len(lines) == 1
    assert lines[0] == 'Hello world'


# ---------------------------------------------------------------------------
# Cycle 2: Multiple transcriptions append sequentially
# ---------------------------------------------------------------------------

def test_multiple_transcriptions_append_sequentially(tmp_path):
    filepath = tmp_path / 'transcriptions.txt'

    append_transcription(str(filepath), 'First sentence')
    append_transcription(str(filepath), 'Second sentence')
    append_transcription(str(filepath), 'Third sentence')

    lines = filepath.read_text().splitlines()
    assert lines == ['First sentence', 'Second sentence', 'Third sentence']


# ---------------------------------------------------------------------------
# Cycle 3: Shared buffer write and read
# ---------------------------------------------------------------------------

def test_shared_buffer_write_and_read():
    buf = SharedAudioBuffer(maxlen=100)

    frame1 = np.ones(1280, dtype=np.int16)
    frame2 = np.ones(1280, dtype=np.int16) * 2

    buf.put(frame1)
    buf.put(frame2)

    out1 = buf.get()
    out2 = buf.get()

    assert out1 is not None
    assert out2 is not None
    assert np.array_equal(out1, frame1)
    assert np.array_equal(out2, frame2)


# ---------------------------------------------------------------------------
# Cycle 4: Non-blocking read when empty
# ---------------------------------------------------------------------------

def test_shared_buffer_nonblocking_when_empty():
    buf = SharedAudioBuffer(maxlen=100)

    result = buf.get()

    assert result is None, f'Expected None from empty buffer, got {type(result)}'


# ---------------------------------------------------------------------------
# Cycle 5: Wake word mode toggle
# ---------------------------------------------------------------------------

def test_wake_word_mode_toggle():
    mode = WakeWordMode()

    # Default: disabled
    assert not mode.is_enabled

    # Enable
    mode.enable()
    assert mode.is_enabled

    # Disable
    mode.disable()
    assert not mode.is_enabled

    # Toggle
    mode.toggle()
    assert mode.is_enabled
    mode.toggle()
    assert not mode.is_enabled
