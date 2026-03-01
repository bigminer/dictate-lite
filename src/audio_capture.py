"""Shared audio capture and probe helpers."""

from __future__ import annotations

import numpy as np


def probe_input_stream(sd_module, device_index, sample_rate=16000, channels=1, dtype='float32', blocksize=1024):
    """Open/start/stop/close a stream to validate microphone availability."""
    stream = sd_module.InputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype=dtype,
        blocksize=blocksize,
        device=device_index
    )
    stream.start()
    stream.stop()
    stream.close()


def capture_from_stream(
    sd_module,
    device_index,
    seconds,
    sample_rate=16000,
    channels=1,
    dtype='float32',
    blocksize=1024,
    logger=None,
):
    """Capture fixed-duration audio using blocking stream reads.

    Returns a flattened 1D numpy array (float32).
    """
    total_frames = int(seconds * sample_rate)
    chunks = []
    remaining = total_frames

    with sd_module.InputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype=dtype,
        blocksize=blocksize,
        device=device_index
    ) as stream:
        while remaining > 0:
            to_read = min(blocksize, remaining)
            data, overflowed = stream.read(to_read)
            if overflowed and logger:
                logger.warning('Input overflow detected during recording.')
            chunks.append(np.array(data, copy=True))
            remaining -= len(data)

    if not chunks:
        return np.array([], dtype=np.float32)

    return np.concatenate(chunks, axis=0).flatten()


def compute_rms(audio):
    """Compute RMS energy of a float32 audio array."""
    return float(np.sqrt(np.mean(audio ** 2)))
