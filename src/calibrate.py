"""
Noise Gate Calibration Tool
Records ambient noise and speech to automatically calculate optimal threshold.
"""

import sys
import os
import time
import numpy as np

# Add src directory to path for config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import sounddevice as sd
except ImportError:
    print("ERROR: sounddevice not installed")
    print("Run: pip install sounddevice")
    sys.exit(1)

# Load audio device from config if available
try:
    from config import AUDIO_DEVICE
except ImportError:
    AUDIO_DEVICE = None

SAMPLE_RATE = 16000
AMBIENT_DURATION = 3.0  # seconds
SPEECH_DURATION = 4.0   # seconds


def resolve_audio_device():
    """Resolve AUDIO_DEVICE to a device index with fallback chain.
    Handles string names (current format), integer indices (legacy), and None (default).
    Returns (device_index_or_None, device_name_str).
    """
    global AUDIO_DEVICE
    devices = sd.query_devices()
    input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]

    if not input_devices:
        print("ERROR: No input devices found on this system")
        sys.exit(1)

    if AUDIO_DEVICE is not None:
        if isinstance(AUDIO_DEVICE, str):
            # Name-based lookup: exact match then substring
            for idx, dev in input_devices:
                if dev['name'] == AUDIO_DEVICE:
                    return idx, dev['name']
            for idx, dev in input_devices:
                if AUDIO_DEVICE in dev['name'] or dev['name'] in AUDIO_DEVICE:
                    print(f"  Matched device by substring: '{AUDIO_DEVICE}' -> [{idx}] '{dev['name']}'")
                    return idx, dev['name']
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
    default_idx = sd.default.device[0]
    if default_idx is not None and default_idx >= 0:
        try:
            device_info = sd.query_devices(default_idx)
            if device_info['max_input_channels'] > 0:
                return default_idx, device_info['name']
        except Exception:
            pass

    # Last resort: first available input device
    first_idx, first_dev = input_devices[0]
    return first_idx, first_dev['name']


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
        with open(config_path, 'r') as f:
            content = f.read()

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

        with open(config_path, 'w') as f:
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
