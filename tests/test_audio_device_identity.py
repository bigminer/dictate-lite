"""Unit tests for shared audio device identity helpers."""

import audio_device_identity as adi


class _FakeSoundDevice:
    class _Default:
        device = (1, 2)

    default = _Default()

    @staticmethod
    def query_hostapis():
        return [
            {'name': 'MME'},
            {'name': 'Windows WASAPI'},
        ]

    @staticmethod
    def query_devices(index=None):
        devices = [
            {
                'name': 'Speakers',
                'max_input_channels': 0,
                'max_output_channels': 2,
                'hostapi': 0,
                'default_samplerate': 48000,
                'default_low_input_latency': 0.01,
                'default_high_input_latency': 0.1,
            },
            {
                'name': 'USB Mic',
                'max_input_channels': 1,
                'max_output_channels': 0,
                'hostapi': 1,
                'default_samplerate': 48000,
                'default_low_input_latency': 0.01,
                'default_high_input_latency': 0.1,
            },
            {
                'name': 'Built-in Mic',
                'max_input_channels': 2,
                'max_output_channels': 0,
                'hostapi': 0,
                'default_samplerate': 44100,
                'default_low_input_latency': 0.02,
                'default_high_input_latency': 0.2,
            },
        ]
        if index is None:
            return devices
        return devices[index]


def test_enumerate_input_devices_filters_to_inputs():
    devices = adi.enumerate_input_devices(_FakeSoundDevice)
    assert len(devices) == 2
    assert devices[0][1] == 'USB Mic'
    assert devices[1][1] == 'Built-in Mic'
    assert len(devices[0][4]) == 20


def test_choose_candidate_prefers_preferred_index():
    candidates = [
        (1, 'A', 'MME', 0, 'uid-a'),
        (2, 'B', 'Windows WASAPI', 1, 'uid-b'),
    ]
    chosen = adi.choose_candidate(candidates, preferred_index=2, default_index=1)
    assert chosen[0] == 2


def test_resolve_device_name_exact_then_partial():
    input_devices = [
        (1, 'USB Mic', 'Windows WASAPI', 1, 'uid-1'),
        (2, 'Built-in Mic', 'MME', 0, 'uid-2'),
    ]
    exact = adi.resolve_device_name('USB Mic', input_devices)
    partial = adi.resolve_device_name('Built', input_devices)
    assert exact[0] == 1
    assert partial[0] == 2


def test_resolve_device_uid():
    input_devices = [
        (1, 'USB Mic', 'Windows WASAPI', 1, 'uid-1'),
        (2, 'Built-in Mic', 'MME', 0, 'uid-2'),
    ]
    resolved = adi.resolve_device_uid('uid-2', input_devices)
    assert resolved[0] == 2
    assert resolved[1] == 'Built-in Mic'


def test_resolve_preferred_input_device_falls_back_to_default():
    input_devices = adi.enumerate_input_devices(_FakeSoundDevice)
    resolved = adi.resolve_preferred_input_device(
        _FakeSoundDevice,
        input_devices,
        saved_name='Missing Device',
        saved_hostapi='MME',
        saved_index=99,
        saved_uid='missing-uid'
    )
    assert resolved[0] == 1
    assert resolved[1] == 'USB Mic'


def test_enumerate_and_resolve_returns_device_and_list():
    idx, name, hostapi, uid, devices = adi.enumerate_and_resolve(
        _FakeSoundDevice,
        saved_name='USB Mic',
    )
    assert idx == 1
    assert name == 'USB Mic'
    assert len(devices) == 2


def test_enumerate_and_resolve_returns_none_when_no_match():
    idx, name, hostapi, uid, devices = adi.enumerate_and_resolve(
        _FakeSoundDevice,
        saved_name='Nonexistent',
        saved_uid='bad-uid',
    )
    # Falls back to default device (index 1 = USB Mic per _FakeSoundDevice)
    assert idx == 1
    assert len(devices) == 2

