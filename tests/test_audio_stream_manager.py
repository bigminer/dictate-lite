"""Unit tests for AudioStreamManager."""

from audio_stream_manager import AudioStreamManager


class _FakeStream:
    def __init__(self):
        self.active = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.active = True

    def stop(self):
        self.stopped = True
        self.active = False

    def close(self):
        self.closed = True


class _FakeSoundDevice:
    def __init__(self):
        self.created = []

    def InputStream(self, **kwargs):
        stream = _FakeStream()
        self.created.append((kwargs, stream))
        return stream


def _noop_callback(*_args, **_kwargs):
    return None


def test_open_sets_current_stream_and_device():
    sd = _FakeSoundDevice()
    manager = AudioStreamManager(sd, _noop_callback)
    stream = manager.open(3)
    assert stream.active is True
    assert manager.stream is stream
    assert manager.current_device == 3
    assert manager.is_active is True


def test_switch_keeps_new_and_closes_old():
    sd = _FakeSoundDevice()
    manager = AudioStreamManager(sd, _noop_callback)
    first = manager.open(1)
    old_stream, old_device = manager.switch(2)

    assert old_stream is first
    assert old_device == 1
    assert first.stopped is True
    assert first.closed is True
    assert manager.current_device == 2
    assert manager.is_active is True


def test_close_clears_stream():
    sd = _FakeSoundDevice()
    manager = AudioStreamManager(sd, _noop_callback)
    manager.open(4)
    manager.close()
    assert manager.stream is None
    assert manager.current_device is None
    assert manager.is_active is False
