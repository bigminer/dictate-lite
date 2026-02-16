"""
Startup healthcheck for Voice Dictation.

Runs an operational readiness check in the startup command window:
1) Resolve and open microphone stream
2) Prompt user to say "check 1 2 3"
3) Transcribe sample audio
4) Report pass/fail with actionable guidance
"""

import os
import re
import sys
import tempfile
import time
import argparse

import numpy as np
import sounddevice as sd
import soundfile as sf

import audio_device_identity as audio_identity
import runtime_state


TARGET_PROMPT = 'check 1 2 3'
SAMPLE_RATE = 16000
RECORD_SECONDS = 3.5
MAX_PHRASE_ATTEMPTS = 3


def load_config():
    """Load runtime config with safe defaults."""
    cfg = {
        'HOTKEY': 'alt+f',
        'MODEL_SIZE': 'small',
        'LANGUAGE': 'en',
        'DEVICE': 'cpu',
        'COMPUTE_TYPE': 'int8',
        'AUDIO_DEVICE': None,
        'AUDIO_DEVICE_HOSTAPI': None,
        'AUDIO_DEVICE_INDEX': None,
        'AUDIO_DEVICE_UID': None,
    }

    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    try:
        import config as user_config  # type: ignore
    except Exception as e:
        print(f'[WARN] Could not load src/config.py ({type(e).__name__}: {e})')
        print('[WARN] Using default healthcheck settings.')
        return cfg

    for key in cfg:
        if hasattr(user_config, key):
            cfg[key] = getattr(user_config, key)

    if isinstance(cfg['AUDIO_DEVICE_INDEX'], str):
        stripped = cfg['AUDIO_DEVICE_INDEX'].strip()
        cfg['AUDIO_DEVICE_INDEX'] = int(stripped) if stripped.isdigit() else None

    if isinstance(cfg['AUDIO_DEVICE_UID'], str):
        cfg['AUDIO_DEVICE_UID'] = cfg['AUDIO_DEVICE_UID'].strip() or None

    return cfg


def load_runtime_state():
    """Load prior runtime state written by dictate.py."""
    return runtime_state.read_runtime_state()


def enumerate_input_devices():
    """Return input device tuples: (index, name, hostapi_name, device_uid)."""
    devices = audio_identity.enumerate_input_devices(sd)
    return [(idx, name, hostapi_name, device_uid) for idx, name, hostapi_name, _, device_uid in devices]


def resolve_device(cfg, input_devices):
    """Resolve configured device identity with dock/undock-friendly fallback chain."""
    saved_name = cfg['AUDIO_DEVICE']
    saved_hostapi = cfg['AUDIO_DEVICE_HOSTAPI']
    saved_index = cfg['AUDIO_DEVICE_INDEX']
    saved_uid = cfg['AUDIO_DEVICE_UID']
    resolved = audio_identity.resolve_preferred_input_device(
        sd,
        [(idx, name, hostapi_name, None, uid) for idx, name, hostapi_name, uid in input_devices],
        saved_name=saved_name,
        saved_hostapi=saved_hostapi,
        saved_index=saved_index,
        saved_uid=saved_uid
    )
    idx, name, hostapi_name, uid = resolved
    if idx is None:
        return None
    return idx, name, hostapi_name, uid


def stream_probe(device_index):
    """Open/close an input stream to verify microphone availability."""
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='float32',
        blocksize=1024,
        device=device_index
    )
    stream.start()
    stream.stop()
    stream.close()


def record_phrase(device_index):
    """Record audio sample for phrase verification."""
    print(f'[INFO] Recording for {RECORD_SECONDS:.1f}s...')
    total_frames = int(RECORD_SECONDS * SAMPLE_RATE)
    blocksize = 1024
    chunks = []
    remaining = total_frames

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='float32',
        blocksize=blocksize,
        device=device_index
    ) as stream:
        while remaining > 0:
            to_read = min(blocksize, remaining)
            data, overflowed = stream.read(to_read)
            if overflowed:
                print('[WARN] Input overflow detected during healthcheck recording.')
            chunks.append(np.array(data, copy=True))
            remaining -= len(data)

    if not chunks:
        return np.array([], dtype=np.float32)

    return np.concatenate(chunks, axis=0).flatten()


def normalize_text(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9 ]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def phrase_matched(text):
    norm = normalize_text(text)
    accepted = (
        'check 1 2 3',
        'check one two three',
        'check one to three',
        'check one two tree',
    )
    return any(p in norm for p in accepted)


def transcribe_audio(cfg, audio):
    """Transcribe recorded audio with runtime model settings and safe fallback."""
    from faster_whisper import WhisperModel

    model_size = cfg['MODEL_SIZE'] or 'small'
    device = cfg['DEVICE'] or 'cpu'
    compute_type = cfg['COMPUTE_TYPE'] or ('float16' if device == 'cuda' else 'int8')
    language = None if cfg['LANGUAGE'] == 'auto' else cfg['LANGUAGE']

    model = None
    try:
        print(f"[INFO] Loading model '{model_size}' on {device}...")
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"[WARN] Model load failed ({type(e).__name__}: {e})")
        print("[WARN] Falling back to tiny model on CPU for healthcheck...")
        model = WhisperModel('tiny', device='cpu', compute_type='int8')
        language = None

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_path = f.name
        sf.write(temp_path, audio, SAMPLE_RATE)
        segments, _ = model.transcribe(temp_path, beam_size=3, language=language)
        return ' '.join(seg.text for seg in segments).strip()
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def run_healthcheck():
    print('============================================')
    print(' Voice Dictation - Operational Healthcheck')
    print('============================================')
    print()

    cfg = load_config()
    print(f"[INFO] Python: {sys.version.split()[0]}")
    print(f"[INFO] Configured hotkey: {cfg['HOTKEY']}")
    print(f"[INFO] Configured model: {cfg['MODEL_SIZE']}")

    previous_state = load_runtime_state()
    if previous_state:
        last_status = previous_state.get('status', 'unknown')
        last_reason = previous_state.get('reason')
        last_updated = previous_state.get('updated_at', 'unknown time')
        print(f"[INFO] Last runtime state: {last_status} ({last_updated})")
        if last_reason:
            print(f"[INFO] Last runtime reason: {last_reason}")
        if last_status in ('audio_error', 'starting'):
            print('[WARN] Previous run ended in an unhealthy state.')
            print('       This healthcheck will verify if audio is now operational.')

    input_devices = enumerate_input_devices()
    if not input_devices:
        print('[FAIL] No input microphones found.')
        print('       Connect a microphone and try again.')
        return False

    chosen = resolve_device(cfg, input_devices)
    if not chosen:
        print('[FAIL] Could not resolve a usable microphone device.')
        return False

    device_index, device_name, hostapi_name, device_uid = chosen
    print(f"[INFO] Selected microphone: [{device_index}] {device_name} ({hostapi_name})")
    print(f"[INFO] Device UID: {device_uid}")

    try:
        stream_probe(device_index)
        print('[PASS] Microphone stream open/close probe passed.')
    except Exception as e:
        print(f"[FAIL] Unable to open selected microphone ({type(e).__name__}: {e})")
        return False

    print()
    print(f"Say this phrase clearly when prompted: \"{TARGET_PROMPT}\"")
    for attempt in range(1, MAX_PHRASE_ATTEMPTS + 1):
        print()
        print(f"[INFO] Phrase check attempt {attempt}/{MAX_PHRASE_ATTEMPTS}")
        try:
            input('Press ENTER to begin recording...')
        except EOFError:
            print('[FAIL] Interactive input is not available in this session.')
            print('       Re-run in a normal terminal window to complete phrase verification.')
            return False

        print('Recording starts in 3...')
        time.sleep(1)
        print('2...')
        time.sleep(1)
        print('1...')
        time.sleep(1)

        try:
            audio = record_phrase(device_index)
        except Exception as e:
            print(f"[FAIL] Recording failed ({type(e).__name__}: {e})")
            return False

        rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
        print(f"[INFO] Captured audio RMS: {rms:.6f}")
        if rms < 1e-6:
            print('[WARN] Captured silence. Check mic mute/privacy settings and try again.')
            if attempt < MAX_PHRASE_ATTEMPTS:
                continue
            print('[FAIL] Phrase verification failed after repeated silence.')
            return False

        try:
            text = transcribe_audio(cfg, audio)
        except Exception as e:
            print(f"[FAIL] Transcription failed ({type(e).__name__}: {e})")
            return False

        print(f"[INFO] Heard: {text if text else '<empty>'}")

        if phrase_matched(text):
            print()
            print('[PASS] Healthcheck succeeded.')
            print('       Voice detection is operational. You can close this window.')
            return True

        print(f"[WARN] Phrase mismatch on attempt {attempt}.")
        if attempt < MAX_PHRASE_ATTEMPTS:
            print('       Please try again and speak clearly near the microphone.')

    print()
    print('[FAIL] Phrase verification did not match expected text.')
    print(f"       Expected something like: \"{TARGET_PROMPT}\"")
    print('       You can still launch, but dictation quality may be degraded.')
    return False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Voice Dictation startup healthcheck')
    parser.add_argument(
        '--healthcheck-only',
        action='store_true',
        help='Run healthcheck and exit (does not launch dictation).'
    )
    parser.add_argument(
        '--skip-healthcheck',
        action='store_true',
        help='Skip all health checks and return success.'
    )
    return parser.parse_args(argv)


if __name__ == '__main__':
    args = parse_args()
    if args.skip_healthcheck:
        print('[INFO] Healthcheck skipped by flag (--skip-healthcheck).')
        sys.exit(0)

    ok = run_healthcheck()
    if args.healthcheck_only:
        print('[INFO] Healthcheck-only mode complete.')
    sys.exit(0 if ok else 1)
