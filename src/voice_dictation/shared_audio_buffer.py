"""Thread-safe shared audio buffer for wake word and recording pipelines."""

from __future__ import annotations

import collections


class SharedAudioBuffer:
    """FIFO buffer for audio frames shared between producer and consumer threads."""

    def __init__(self, maxlen: int = 500):
        self._buf: collections.deque = collections.deque(maxlen=maxlen)

    def put(self, frame) -> None:
        """Add a frame to the buffer."""
        self._buf.append(frame)

    def get(self):
        """Remove and return the oldest frame, or None if empty."""
        try:
            return self._buf.popleft()
        except IndexError:
            return None
