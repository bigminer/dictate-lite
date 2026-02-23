"""Centralized input stream lifecycle management."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AudioStreamManager:
    """Manage an active sounddevice InputStream with safe switching."""

    def __init__(
        self,
        sd_module,
        callback,
        sample_rate=16000,
        channels=1,
        dtype='float32',
        blocksize=1024,
        logger=None,
    ):
        self._sd = sd_module
        self._callback = callback
        self._sample_rate = sample_rate
        self._channels = channels
        self._dtype = dtype
        self._blocksize = blocksize
        self._logger = logger
        self.stream = None
        self.current_device = None

    @property
    def is_active(self):
        """Return True when a stream exists and reports active."""
        try:
            return bool(self.stream and self.stream.active)
        except Exception:
            logger.debug("Stream active check failed", exc_info=True)
            return False

    def _build_stream(self, device):
        return self._sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype=self._dtype,
            callback=self._callback,
            blocksize=self._blocksize,
            device=device,
        )

    def close(self):
        """Stop and close current stream if present."""
        if self.stream is None:
            return
        try:
            self.stream.stop()
        except Exception:
            logger.debug("Stream stop failed", exc_info=True)
        try:
            self.stream.close()
        except Exception:
            logger.debug("Stream close failed", exc_info=True)
        self.stream = None
        self.current_device = None

    def open(self, device):
        """Open and start a stream for the provided device."""
        stream = self._build_stream(device)
        stream.start()
        self.stream = stream
        self.current_device = device
        return stream

    def switch(self, device):
        """Switch to a new stream; keep old stream if new one fails."""
        old_stream = self.stream
        old_device = self.current_device

        new_stream = self._build_stream(device)
        new_stream.start()

        self.stream = new_stream
        self.current_device = device

        if old_stream is not None:
            try:
                old_stream.stop()
            except Exception:
                logger.debug("Old stream stop failed", exc_info=True)
            try:
                old_stream.close()
            except Exception:
                logger.debug("Old stream close failed", exc_info=True)

        return old_stream, old_device

    def reopen(self, device):
        """Close current stream then reopen on the requested device."""
        self.close()
        return self.open(device)
