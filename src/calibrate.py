"""
Noise Gate Calibration Tool
Records ambient noise and speech to automatically calculate optimal threshold.
"""

import sys
import os
import time
import hashlib
import numpy as np

# Add src directory to path for config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import sounddevice as sd
except ImportError:
    print("ERROR: sounddevice not installed")
    print("Run: pip install sounddevice")
    sys.exit(1)

# Load audio device identity from config if available
try:
    from config import AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID
except Exception:
    AUDIO_DEVICE = None
    AUDIO_DEVICE_HOSTAPI = None
    AUDIO_DEVICE_INDEX = None
    AUDIO_DEVICE_UID = None

if isinstance(AUDIO_DEVICE_INDEX, str):
    stripped = AUDIO_DEVICE_INDEX.strip()
    AUDIO_DEVICE_INDEX = int(stripped) if stripped.isdigit() else None

if isinstance(AUDIO_DEVICE_UID, str):
    AUDIO_DEVICE_UID = AUDIO_DEVICE_UID.strip() or None

SAMPLE_RATE = 16000
AMBIENT_DURATION = 3.0  # seconds
SPEECH_DURATION = 4.0   # seconds


def _normalize_device_name(name):
    return ' '.join(str(name).strip().lower().split())


def _build_device_uid(device_name, hostapi_name, device_info):
    max_input = int(device_info.get('max_input_channels') or 0)
    max_output = int(device_info.get('max_output_channels') or 0)

    def _fmt_float(value):
        try:
            return f"{float(value):.3f}"
        except Exception:
            return 'na'

    fingerprint = '|'.join([
        _normalize_device_name(device_name),
        str(hostapi_name or ''),
        str(max_input),
        str(max_output),
        _fmt_float(device_info.get('default_samplerate')),
        _fmt_float(device_info.get('default_low_input_latency')),
        _fmt_float(device_info.get('default_high_input_latency')),
    ])
    return hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:20]


def _enumerate_input_devices():
    """Return list of input devices as tuples: (index, name, hostapi_name, device_uid)."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    result = []
    for idx, dev in enumerate(devices):
        if dev['max_input_channels'] <= 0:
            continue
        hostapi_index = dev.get('hostapi')
        hostapi_name = 'Unknown'
        if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(hostapis):
            hostapi_name = hostapis[hostapi_index]['name']
        device_uid = _build_device_uid(dev['name'], hostapi_name, dev)
        result.append((idx, dev['name'], hostapi_name, device_uid))
    return result


def _choose_candidate(candidates, preferred_index=None, default_index=None):
    if not candidates:
        return None
    if preferred_index is not None:
        for c in candidates:
            if c[0] == preferred_index:
                return c
    if default_index is not None:
        for c in candidates:
            if c[0] == default_index:
                return c
    wasapi = [c for c in candidates if c[2] == 'Windows WASAPI']
    if wasapi:
        return wasapi[0]
    return candidates[0]


def resolve_audio_device():
    """Resolve configured device identity with fallback chain across host APIs."""
    global AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID
    input_devices = _enumerate_input_devices()

    if not input_devices:
        print("ERROR: No input devices found on this system")
        sys.exit(1)

    default_idx = sd.default.device[0]
    if not isinstance(default_idx, int) or default_idx < 0:
        default_idx = None

    if AUDIO_DEVICE_UID:
        uid_matches = [d for d in input_devices if d[3] == AUDIO_DEVICE_UID]
        chosen = _choose_candidate(uid_matches, preferred_index=AUDIO_DEVICE_INDEX, default_index=default_idx)
        if chosen:
            return chosen[0], chosen[1]
        print(f"  WARNING: Saved AUDIO_DEVICE_UID '{AUDIO_DEVICE_UID}' not found, falling back")

    if AUDIO_DEVICE is not None:
        if isinstance(AUDIO_DEVICE, str):
            exact = [d for d in input_devices if d[1] == AUDIO_DEVICE]
            if AUDIO_DEVICE_HOSTAPI:
                exact_host = [d for d in exact if d[2] == AUDIO_DEVICE_HOSTAPI]
                if exact_host:
                    exact = exact_host
            chosen = _choose_candidate(exact, preferred_index=AUDIO_DEVICE_INDEX, default_index=default_idx)
            if chosen:
                if len(exact) > 1:
                    print(
                        f"  NOTE: Multiple exact matches for '{AUDIO_DEVICE}', using [{chosen[0]}] "
                        f"'{chosen[1]}' ({chosen[2]})"
                    )
                return chosen[0], chosen[1]

            partial = [d for d in input_devices if AUDIO_DEVICE in d[1] or d[1] in AUDIO_DEVICE]
            if AUDIO_DEVICE_HOSTAPI:
                partial_host = [d for d in partial if d[2] == AUDIO_DEVICE_HOSTAPI]
                if partial_host:
                    partial = partial_host
            chosen = _choose_candidate(partial, preferred_index=AUDIO_DEVICE_INDEX, default_index=default_idx)
            if chosen:
                print(
                    f"  Matched device by substring: '{AUDIO_DEVICE}' -> "
                    f"[{chosen[0]}] '{chosen[1]}' ({chosen[2]})"
                )
                return chosen[0], chosen[1]

            print(f"  WARNING: Saved device '{AUDIO_DEVICE}' not found, falling back to default")
        elif isinstance(AUDIO_DEVICE, int):
            print(f"  NOTE: AUDIO_DEVICE is a legacy integer index ({AUDIO_DEVICE})")
            try:
                device_info = sd.query_devices(AUDIO_DEVICE)
                if device_info['max_input_channels'] > 0:
                    return AUDIO_DEVICE, device_info['name']
            except Exception:
                print(f"  WARNING: Legacy device index {AUDIO_DEVICE} unavailable, falling back")

    # Fall back to system default
    if default_idx is not None:
        try:
            device_info = sd.query_devices(default_idx)
            if device_info['max_input_channels'] > 0:
                return default_idx, device_info['name']
        except Exception:
            pass

    # Last resort: first available input device
    first_idx, first_name, _, _ = input_devices[0]
    return first_idx, first_name


def record_audio(duration, prompt, device_index):
    """Record audio for specified duration with countdown."""
    print(f"\n{prompt}")
    print(f"Recording starts in: ", end='', flush=True)

    for i in range(3, 0, -1):
        print(f"{i}...", end='', flush=True)
        time.sleep(1)
    print("GO!")

    # Record with try/except for device open failures
    try:
        recording = sd.rec(
            int(duration * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            device=device_index
        )
    except Exception as e:
        print(f"\n  ERROR: Failed to open audio device for recording: {e}")
        print("  Please check your microphone connection and try again.")
        sys.exit(1)

    # Show progress
    for i in range(int(duration)):
        time.sleep(1)
        print(f"  Recording... {i+1}/{int(duration)}s", end='\r')

    sd.wait()
    print(f"  Recording complete! ({duration}s)   ")

    return recording.flatten()


def calculate_rms(audio):
    """Calculate RMS level of audio."""
    return np.sqrt(np.mean(audio ** 2))


def calculate_peak(audio):
    """Calculate peak level of audio."""
    return np.max(np.abs(audio))


def update_config(threshold):
    """Update or create config.py with new threshold."""
    config_path = os.path.join(os.path.dirname(__file__), 'config.py')

    # Read existing config if it exists
    if os.path.exists(config_path):
        content = None
        for encoding in ('utf-8', 'cp1252'):
            try:
                with open(config_path, 'r', encoding=encoding) as f:
                    content = f.read()
                if encoding != 'utf-8':
                    print(f"WARNING: config.py decoded as {encoding}; rewriting as UTF-8")
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            with open(config_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            print("WARNING: config.py had invalid encoding; rewriting with replacement chars")

        # Check if NOISE_GATE_THRESHOLD already exists
        if 'NOISE_GATE_THRESHOLD' in content:
            # Replace existing value
            import re
            content = re.sub(
                r'NOISE_GATE_THRESHOLD\s*=\s*[\d.]+',
                f'NOISE_GATE_THRESHOLD = {threshold}',
                content
            )
        else:
            # Append new setting
            content += f"\n# Noise gate threshold (auto-calibrated)\nNOISE_GATE_THRESHOLD = {threshold}\n"

        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"\nUpdated {config_path}")
    else:
        print(f"\nERROR: config.py not found at {config_path}")
        print("Please run install.bat first or create config.py manually.")
        return False

    return True


def main():
    print("=" * 60)
    print("  NOISE GATE CALIBRATION")
    print("=" * 60)

    # Resolve device with fallback chain
    device_index, device_name = resolve_audio_device()
    print(f"\n  Microphone: {device_name} [index {device_index}]")

    print("\n  This tool automatically sets your noise gate threshold.")
    print("  You do NOT need to use the dictation hotkey.")
    print("\n  Two recordings will be made:")
    print("    1. STAY QUIET - captures ambient/background noise (3 sec)")
    print("    2. SPEAK NORMALLY - say a short phrase (4 sec)")
    print("\n  After calibration, restart Voice Dictation to apply.")
    print("\n" + "-" * 60)
    input("  Press ENTER to begin calibration...")

    # Step 1: Record ambient noise
    ambient_audio = record_audio(
        AMBIENT_DURATION,
        "STEP 1: Stay quiet. Recording ambient noise...",
        device_index
    )
    ambient_rms = calculate_rms(ambient_audio)
    ambient_peak = calculate_peak(ambient_audio)

    print(f"\n  Ambient RMS:  {ambient_rms:.4f}")
    print(f"  Ambient Peak: {ambient_peak:.4f}")

    input("\nPress Enter to continue to speech recording...")

    # Step 2: Record speech
    speech_audio = record_audio(
        SPEECH_DURATION,
        "STEP 2: Speak normally. Say: 'Just focus on my voice'",
        device_index
    )
    speech_rms = calculate_rms(speech_audio)
    speech_peak = calculate_peak(speech_audio)

    print(f"\n  Speech RMS:  {speech_rms:.4f}")
    print(f"  Speech Peak: {speech_peak:.4f}")

    # Calculate threshold
    # Set at 30% of the gap between ambient and speech
    # This gives margin above ambient but well below normal speech
    threshold = ambient_rms + (speech_rms - ambient_rms) * 0.3

    # Ensure minimum threshold
    threshold = max(threshold, 0.005)

    # Round to 4 decimal places
    threshold = round(threshold, 4)

    # Show results
    print("\n" + "=" * 50)
    print("  Results")
    print("=" * 50)
    print(f"\n  Ambient RMS:     {ambient_rms:.4f}")
    print(f"  Speech RMS:      {speech_rms:.4f}")

    # Guard against division by zero when ambient is silent
    if ambient_rms > 0:
        print(f"  Ratio:           {speech_rms/ambient_rms:.1f}x louder")
    else:
        print(f"  Ratio:           N/A (ambient was silent)")

    print(f"\n  Recommended threshold: {threshold}")

    # Sanity check
    if speech_rms < ambient_rms * 1.5:
        print("\n  WARNING: Speech was not much louder than ambient noise.")
        print("  Consider speaking louder or reducing background noise.")

    # Offer to save
    print("\n" + "-" * 50)
    response = input(f"Save NOISE_GATE_THRESHOLD = {threshold} to config.py? [Y/n]: ").strip().lower()

    if response in ('', 'y', 'yes'):
        if update_config(threshold):
            print("\nCalibration complete!")
            print("Please restart Voice Dictation for changes to take effect.")
    else:
        print(f"\nNot saved. To manually set, add to config.py:")
        print(f"  NOISE_GATE_THRESHOLD = {threshold}")

    print()
    input("Press Enter to exit...")


if __name__ == '__main__':
    main()
