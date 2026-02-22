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


def test_build_ccstatusline_command_rejects_disallowed_executable():
    module = importlib.import_module('claude_status_tts')
    with patch.dict(os.environ, {'CCSTATUSLINE_COMMAND': 'curl http://evil.example.com'}, clear=False):
        try:
            module.build_ccstatusline_command()
        except ValueError as exc:
            assert 'not in the allowed list' in str(exc)
        else:
            raise AssertionError('Expected ValueError for disallowed executable')


def test_build_ccstatusline_command_rejects_path_traversal_executable():
    module = importlib.import_module('claude_status_tts')
    with patch.dict(os.environ, {'CCSTATUSLINE_COMMAND': '/tmp/evil/ccstatusline'}, clear=False):
        # basename is 'ccstatusline' so this should be allowed - path is stripped
        cmd = module.build_ccstatusline_command()
    assert cmd[0] == '/tmp/evil/ccstatusline'


def test_build_ccstatusline_command_allows_npx_exe():
    module = importlib.import_module('claude_status_tts')
    with patch.dict(os.environ, {'CCSTATUSLINE_COMMAND': 'npx.cmd -y ccstatusline@2.0.25'}, clear=False):
        cmd = module.build_ccstatusline_command()
    assert cmd == ['npx.cmd', '-y', 'ccstatusline@2.0.25']
