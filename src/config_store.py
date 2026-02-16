"""Helpers for reading and updating src/config.py safely."""

from __future__ import annotations

import os
import re
import tempfile


def atomic_write_text(path, content, encoding='utf-8'):
    """Write text atomically (temp file + replace)."""
    directory = os.path.dirname(path)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding=encoding,
            delete=False,
            dir=directory,
            prefix='config.',
            suffix='.tmp'
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def read_text_with_fallback(path, encodings=('utf-8', 'cp1252')):
    """Read a text file trying multiple encodings. Returns (content, encoding_used)."""
    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as handle:
                return handle.read(), encoding
        except UnicodeDecodeError:
            continue

    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        return handle.read(), 'utf-8-replace'


def format_python_literal(value):
    """Format a Python literal assignment value for config.py."""
    if value is None:
        return 'None'
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace('\\', '\\\\').replace("'", "\\'")
    return f"'{escaped}'"


def upsert_assignment(text, key, literal_value):
    """Replace existing KEY assignment or append if missing."""
    pattern = rf'(?m)^{re.escape(key)}\s*=.*$'
    replacement = f'{key} = {literal_value}'
    if re.search(pattern, text):
        return re.sub(pattern, replacement, text, count=1)
    if not text.endswith('\n'):
        text += '\n'
    return text + replacement + '\n'


def update_config_values(config_path, updates, comments=None, logger=None):
    """Update one or more config assignments in src/config.py.

    updates:
        dict mapping KEY -> Python value.
    comments:
        optional dict mapping KEY -> comment line to insert when key is appended.
    """
    if not os.path.exists(config_path):
        if logger:
            logger.warning(f"config.py not found at {config_path}")
        return False

    content, encoding_used = read_text_with_fallback(config_path)
    if logger and encoding_used not in ('utf-8',):
        logger.warning(f"config.py decoded as {encoding_used}; rewriting as UTF-8")

    comments = comments or {}
    for key, value in updates.items():
        marker = comments.get(key)
        if marker and key not in content and marker not in content:
            if not content.endswith('\n'):
                content += '\n'
            content += marker.rstrip() + '\n'
        content = upsert_assignment(content, key, format_python_literal(value))

    atomic_write_text(config_path, content, encoding='utf-8')
    return True
