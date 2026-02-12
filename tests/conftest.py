"""Shared fixtures for voice dictation tests."""

import os
import sys
import pytest

# Add src directory to path so we can import modules under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def fake_input_devices():
    """Standard set of fake sounddevice device dicts for testing."""
    return [
        (0, {'name': 'Built-in Speakers', 'max_input_channels': 0, 'max_output_channels': 2}),
        (1, {'name': 'Built-in Microphone', 'max_input_channels': 2, 'max_output_channels': 0}),
        (2, {'name': 'USB Headset Mic', 'max_input_channels': 1, 'max_output_channels': 0}),
        (3, {'name': 'Webcam Microphone (HD Pro)', 'max_input_channels': 1, 'max_output_channels': 0}),
    ]


@pytest.fixture
def input_only_devices(fake_input_devices):
    """Only the devices that have input channels (filtered like the real code does)."""
    return [(i, d) for i, d in fake_input_devices if d['max_input_channels'] > 0]


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config.py file for testing persistence."""
    config_file = tmp_path / "config.py"
    config_file.write_text(
        "HOTKEY = 'alt+f'\n"
        "MODEL_SIZE = 'small'\n"
        "DEVICE = 'cuda'\n"
        "COMPUTE_TYPE = 'float16'\n"
        "AUDIO_DEVICE = None\n"
        "LANGUAGE = 'en'\n"
    )
    return config_file
