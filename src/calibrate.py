"""
Noise Gate Calibration Tool
Records ambient noise and speech to automatically calculate optimal threshold.
"""

import sys
import os
import time
import numpy as np
import audio_device_identity as audio_identity

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


def _enumerate_input_devices():
    """Return list of input devices as tuples: (index, name, hostapi_name, device_uid)."""
    devices = audio_identity.enumerate_input_devices(sd)
    return [(idx, name, hostapi_name, device_uid) for idx, name, hostapi_name, _, device_uid in devices]


def resolve_audio_device():
    """Resolve configured device identity with fallback chain across host APIs."""
    global AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID
    input_devices = _enumerate_input_devices()

    if not input_devices:
        print("ERROR: No input devices found on this system")
        sys.exit(1)

    idx, name, hostapi_name, uid = audio_identity.resolve_preferred_input_device(
        sd,
        [(d_idx, d_name, d_hostapi, None, d_uid) for d_idx, d_name, d_hostapi, d_uid in input_devices],
        saved_name=AUDIO_DEVICE,
        saved_hostapi=AUDIO_DEVICE_HOSTAPI,
        saved_index=AUDIO_DEVICE_INDEX,
        saved_uid=AUDIO_DEVICE_UID
    )

    if idx is None:
        print("ERROR: Failed to resolve a usable microphone device")
        sys.exit(1)

    if AUDIO_DEVICE_UID and AUDIO_DEVICE_UID != uid:
        print(f"  NOTE: Saved AUDIO_DEVICE_UID changed: {AUDIO_DEVICE_UID} -> {uid}")
    print(f"  Using microphone [{idx}] '{name}' ({hostapi_name}) uid={uid}")
    return idx, name


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
