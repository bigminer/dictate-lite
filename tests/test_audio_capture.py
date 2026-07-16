"""Unit tests for audio_capture helpers."""

import numpy as np

import audio_capture


class _FakeProbeStream:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakeReadStream:
    def __init__(self):
        self.cursor = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, count):
        values = np.ones((count, 1), dtype=np.float32) * 0.25
        self.cursor += count
        return values, False


class _ProbeSoundDevice:
    def __init__(self):
        self.probe_stream = _FakeProbeStream()

    def InputStream(self, **kwargs):
        return self.probe_stream


class _CaptureSoundDevice:
    def InputStream(self, **kwargs):
        return _FakeReadStream()


def test_probe_input_stream_opens_and_closes():
    sd = _ProbeSoundDevice()
    audio_capture.probe_input_stream(sd, device_index=1)
    assert sd.probe_stream.started is True
    assert sd.probe_stream.stopped is True
    assert sd.probe_stream.closed is True


def test_compute_rms_returns_expected_value():
    audio = np.array([0.3, -0.3, 0.3, -0.3], dtype=np.float32)
    assert abs(audio_capture.compute_rms(audio) - 0.3) < 1e-6


def test_compute_rms_returns_zero_for_silence():
    audio = np.zeros(100, dtype=np.float32)
    assert audio_capture.compute_rms(audio) == 0.0


def test_capture_from_stream_returns_flattened_audio():
    sd = _CaptureSoundDevice()
    audio = audio_capture.capture_from_stream(
        sd,
        device_index=2,
        seconds=0.1,
        sample_rate=100,
        channels=1,
        dtype='float32',
        blocksize=4,
    )
    assert audio.ndim == 1
    assert audio.size == 10
    assert np.allclose(audio, 0.25)
