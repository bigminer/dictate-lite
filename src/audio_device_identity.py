"""
Shared audio input device identity and resolution helpers.
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)


def normalize_device_name(name):
    """Normalize device name for stable identity matching/hashing."""
    return ' '.join(str(name).strip().lower().split())


def _format_float(value):
    try:
        return f"{float(value):.3f}"
    except Exception:
        return 'na'


def build_device_uid(device_name, hostapi_name, device_info):
    """Build a stable UID from microphone metadata."""
    max_input = int(device_info.get('max_input_channels') or 0)
    max_output = int(device_info.get('max_output_channels') or 0)

    fingerprint = '|'.join([
        normalize_device_name(device_name),
        str(hostapi_name or ''),
        str(max_input),
        str(max_output),
        _format_float(device_info.get('default_samplerate')),
        _format_float(device_info.get('default_low_input_latency')),
        _format_float(device_info.get('default_high_input_latency')),
    ])
    return hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:20]


def enumerate_input_devices(sd_module):
    """Return input tuples: (index, name, hostapi_name, hostapi_index, device_uid)."""
    devices = sd_module.query_devices()
    hostapis = sd_module.query_hostapis()
    result = []
    for idx, dev in enumerate(devices):
        if dev['max_input_channels'] <= 0:
            continue
        hostapi_index = dev.get('hostapi')
        hostapi_name = 'Unknown'
        if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(hostapis):
            hostapi_name = hostapis[hostapi_index]['name']
        device_uid = build_device_uid(dev['name'], hostapi_name, dev)
        result.append((idx, dev['name'], hostapi_name, hostapi_index, device_uid))
    return result


def choose_candidate(candidates, preferred_index=None, default_index=None):
    """Choose a device tuple from candidates with deterministic preference order."""
    if not candidates:
        return None

    if preferred_index is not None:
        for candidate in candidates:
            if candidate[0] == preferred_index:
                return candidate

    if default_index is not None:
        for candidate in candidates:
            if candidate[0] == default_index:
                return candidate

    wasapi = [c for c in candidates if c[2] == 'Windows WASAPI']
    if wasapi:
        return wasapi[0]

    return candidates[0]


def resolve_device_name(
    device_name,
    input_devices,
    preferred_hostapi=None,
    preferred_index=None,
    default_index=None
):
    """Resolve name to (index, name, hostapi_name, device_uid)."""
    if not device_name:
        return None, None, None, None

    exact = [d for d in input_devices if d[1] == device_name]
    if preferred_hostapi:
        exact_host = [d for d in exact if d[2] == preferred_hostapi]
        if exact_host:
            exact = exact_host

    chosen = choose_candidate(exact, preferred_index=preferred_index, default_index=default_index)
    if chosen:
        return chosen[0], chosen[1], chosen[2], chosen[4]

    partial = [d for d in input_devices if device_name in d[1] or d[1] in device_name]
    if preferred_hostapi:
        partial_host = [d for d in partial if d[2] == preferred_hostapi]
        if partial_host:
            partial = partial_host

    chosen = choose_candidate(partial, preferred_index=preferred_index, default_index=default_index)
    if chosen:
        return chosen[0], chosen[1], chosen[2], chosen[4]

    return None, None, None, None


def resolve_device_uid(device_uid, input_devices, default_index=None):
    """Resolve UID to (index, name, hostapi_name, device_uid)."""
    if not isinstance(device_uid, str) or not device_uid.strip():
        return None, None, None, None

    matches = [d for d in input_devices if d[4] == device_uid]
    chosen = choose_candidate(matches, default_index=default_index)
    if chosen:
        return chosen[0], chosen[1], chosen[2], chosen[4]

    return None, None, None, None


def current_input_topology_signature(input_devices):
    """Return a deterministic signature for currently available input devices."""
    entries = []
    for idx, name, hostapi_name, _, device_uid in input_devices:
        entries.append((
            str(device_uid or ''),
            str(hostapi_name or ''),
            normalize_device_name(name),
            int(idx)
        ))
    entries.sort()
    return tuple(entries)


def get_default_input_index(sd_module):
    """Return valid default input index or None."""
    default_idx = sd_module.default.device[0]
    if not isinstance(default_idx, int) or default_idx < 0:
        return None
    return default_idx


def resolve_preferred_input_device(
    sd_module,
    input_devices,
    saved_name=None,
    saved_hostapi=None,
    saved_index=None,
    saved_uid=None
):
    """Resolve preferred device with fallback chain to default/first available.

    Returns (index, name, hostapi_name, device_uid) or (None, None, None, None).
    """
    default_idx = get_default_input_index(sd_module)

    if isinstance(saved_uid, str) and saved_uid.strip():
        resolved = resolve_device_uid(saved_uid, input_devices, default_index=default_idx)
        if resolved[0] is not None:
            return resolved

    if isinstance(saved_name, str):
        resolved = resolve_device_name(
            saved_name,
            input_devices,
            preferred_hostapi=saved_hostapi,
            preferred_index=saved_index,
            default_index=default_idx
        )
        if resolved[0] is not None:
            return resolved

    if isinstance(saved_name, int):
        try:
            dev = sd_module.query_devices(saved_name)
            if dev['max_input_channels'] > 0:
                hostapis = sd_module.query_hostapis()
                hostapi_name = 'Unknown'
                hidx = dev.get('hostapi')
                if isinstance(hidx, int) and 0 <= hidx < len(hostapis):
                    hostapi_name = hostapis[hidx]['name']
                return saved_name, dev['name'], hostapi_name, build_device_uid(dev['name'], hostapi_name, dev)
        except Exception:
            logger.debug("UID-based device match failed", exc_info=True)

    if default_idx is not None:
        by_index = [d for d in input_devices if d[0] == default_idx]
        if by_index:
            chosen = by_index[0]
            return chosen[0], chosen[1], chosen[2], chosen[4]

        try:
            dev = sd_module.query_devices(default_idx)
            if dev['max_input_channels'] > 0:
                hostapis = sd_module.query_hostapis()
                hostapi_name = 'Unknown'
                hidx = dev.get('hostapi')
                if isinstance(hidx, int) and 0 <= hidx < len(hostapis):
                    hostapi_name = hostapis[hidx]['name']
                return default_idx, dev['name'], hostapi_name, build_device_uid(dev['name'], hostapi_name, dev)
        except Exception:
            logger.debug("Default device query failed", exc_info=True)

    if input_devices:
        first = input_devices[0]
        return first[0], first[1], first[2], first[4]

    return None, None, None, None
