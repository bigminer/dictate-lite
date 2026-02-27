"""Append transcriptions to a plain text log file."""

from __future__ import annotations


def append_transcription(filepath: str, text: str) -> None:
    """Append a single transcription as one line to the given file."""
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(text + '\n')
