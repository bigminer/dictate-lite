"""Helpers for Whisper model loading and in-memory audio transcription."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata

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


def transcribe_audio_array(model, audio, sample_rate, sf_module, **transcribe_kwargs):
    """Transcribe numpy audio array by writing a temporary WAV file."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as handle:
            temp_path = handle.name
        sf_module.write(temp_path, audio, sample_rate)
        segments, _ = model.transcribe(temp_path, **transcribe_kwargs)
        return ' '.join(segment.text for segment in segments).strip()
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def sanitize_transcript_text(text):
    """Remove control characters and normalize whitespace for safe key injection."""
    if not text:
        return ''

    normalized = unicodedata.normalize('NFKC', str(text))
    cleaned = ''.join(
        ' ' if unicodedata.category(char).startswith('C') else char
        for char in normalized
    )
    return _WHITESPACE_RE.sub(' ', cleaned).strip()
