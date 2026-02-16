"""Tests for src/claude_status_tts.py command hardening."""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch


def test_build_ccstatusline_command_uses_pinned_npx_package_by_default():
    module = importlib.import_module('claude_status_tts')
    with patch.dict(os.environ, {}, clear=False):
        module.CCSTATUSLINE_PACKAGE = 'ccstatusline@2.0.25'
        cmd = module.build_ccstatusline_command()
    assert cmd == ['npx', '-y', 'ccstatusline@2.0.25']


def test_build_ccstatusline_command_rejects_latest_tag():
    module = importlib.import_module('claude_status_tts')
    with patch.dict(os.environ, {}, clear=False):
        module.CCSTATUSLINE_PACKAGE = 'ccstatusline@latest'
        try:
            module.build_ccstatusline_command()
        except ValueError as exc:
            assert 'version-pinned' in str(exc)
        else:
            raise AssertionError('Expected ValueError for non-pinned package tag')


def test_build_ccstatusline_command_honors_explicit_command_override():
    module = importlib.import_module('claude_status_tts')
    with patch.dict(os.environ, {'CCSTATUSLINE_COMMAND': 'ccstatusline --compact'}, clear=False):
        cmd = module.build_ccstatusline_command()
    assert cmd == ['ccstatusline', '--compact']
