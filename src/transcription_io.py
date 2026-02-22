"""Helpers for Whisper model loading and in-memory audio transcription."""

from __future__ import annotations

import re
import unicodedata

import numpy as np

_WHITESPACE_RE = re.compile(r'\s+')


def load_whisper_model(model_size, device, compute_type, fallback_model='tiny', logger=None):
    """Load WhisperModel, falling back to tiny/cpu/int8 on failure."""
    from faster_whisper import WhisperModel

    try:
        if logger:
            logger.info(f"Loading model '{model_size}' on {device}...")
        return WhisperModel(model_size, device=device, compute_type=compute_type), False
    except Exception as exc:
        if logger:
            logger.warning(f"Model load failed ({type(exc).__name__}: {exc})")
            logger.warning('Falling back to tiny model on CPU for healthcheck...')
        return WhisperModel(fallback_model, device='cpu', compute_type='int8'), True


def transcribe_audio_array(model, audio, **transcribe_kwargs):
    """Transcribe numpy audio array by passing it directly to faster-whisper."""
    audio_1d = np.ravel(audio).astype(np.float32, copy=False)
    segments, _ = model.transcribe(audio_1d, **transcribe_kwargs)
    return ' '.join(segment.text for segment in segments).strip()


def sanitize_transcript_text(text):
    """Remove control characters and normalize whitespace for safe key injection."""
    if not text:
        return ''

    raw = text if isinstance(text, str) else str(text)

    # Fast path for the common ASCII-only case.
    if raw.isascii():
        cleaned_ascii = ''.join(
            ' ' if (ord(char) < 32 or ord(char) == 127) else char
            for char in raw
        )
        return _WHITESPACE_RE.sub(' ', cleaned_ascii).strip()

    normalized = unicodedata.normalize('NFKC', raw)
    cleaned = ''.join(
        ' ' if unicodedata.category(char).startswith('C') else char
        for char in normalized
    )
    return _WHITESPACE_RE.sub(' ', cleaned).strip()
