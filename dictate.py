"""
Voice Dictation Tool
Hold Alt+F to record, release to transcribe and type.
Uses faster-whisper with GPU acceleration.
"""

import sys
import threading
import queue
import tempfile
import os
import time
import atexit
import logging
from datetime import datetime

# Set up logging FIRST before any other imports that might fail
LOG_DIR = os.path.join(os.path.expanduser('~'), 'voice-dictation')
LOG_FILE = os.path.join(LOG_DIR, 'dictation.log')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logger.info("=" * 50)
logger.info("Voice Dictation starting...")
logger.info(f"Python: {sys.version}")
logger.info(f"Working dir: {os.getcwd()}")
logger.info(f"Log file: {LOG_FILE}")

try:
    import keyboard
    logger.info("keyboard imported OK")
except Exception as e:
    logger.error(f"Failed to import keyboard: {e}")
    raise

try:
    import sounddevice as sd
    logger.info("sounddevice imported OK")
except Exception as e:
    logger.error(f"Failed to import sounddevice: {e}")
    raise

try:
    import soundfile as sf
    logger.info("soundfile imported OK")
except Exception as e:
    logger.error(f"Failed to import soundfile: {e}")
    raise

try:
    import numpy as np
    logger.info("numpy imported OK")
except Exception as e:
    logger.error(f"Failed to import numpy: {e}")
    raise

try:
    import pyperclip
    logger.info("pyperclip imported OK")
except Exception as e:
    logger.error(f"Failed to import pyperclip: {e}")
    raise

# Single instance lock file
LOCK_FILE = os.path.join(tempfile.gettempdir(), 'voice-dictation.lock')


def check_microphone():
    """Check that a microphone is available."""
    try:
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        if not input_devices:
            print()
            print("ERROR: No microphone found!")
            print()
            print("Please connect a microphone and try again.")
            print("If you have a microphone connected, check:")
            print("  1. Windows Settings > Sound > Input")
            print("  2. Make sure your microphone is selected")
            print("  3. Check that apps have permission to use microphone")
            print()
            input("Press Enter to exit...")
            sys.exit(1)

        # Log available devices
        logger.info(f"Found {len(input_devices)} input device(s)")
        for d in input_devices:
            logger.debug(f"  - {d['name']}")

        # Check the specific device we'll use
        device_idx = AUDIO_DEVICE if AUDIO_DEVICE is not None else sd.default.device[0]
        if device_idx is None:
            print()
            print("ERROR: No default input device configured!")
            print()
            print("Please set a default microphone in:")
            print("  Windows Settings > Sound > Input")
            print()
            input("Press Enter to exit...")
            sys.exit(1)

        device_info = sd.query_devices(device_idx)
        if device_info['max_input_channels'] == 0:
            print()
            print(f"ERROR: Selected device '{device_info['name']}' has no input channels!")
            print()
            print("Please select a microphone device, not a speaker/output.")
            print()
            input("Press Enter to exit...")
            sys.exit(1)

        logger.info(f"Will use input device: {device_info['name']}")
        return device_info

    except Exception as e:
        logger.exception(f"Error checking microphone: {e}")
        print()
        print(f"ERROR: Could not detect audio devices: {e}")
        print()
        print("Make sure your audio drivers are installed.")
        print()
        input("Press Enter to exit...")
        sys.exit(1)


def check_single_instance():
    """Ensure only one instance runs. Exit silently if already running."""
    logger.info(f"Checking single instance. Lock file: {LOCK_FILE}")
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid = int(f.read().strip())
            logger.info(f"Found existing lock file with PID: {pid}")
            # Check if process is still running
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                # Process exists, exit silently
                logger.info(f"Process {pid} is still running. Exiting.")
                sys.exit(0)
            else:
                logger.info(f"Process {pid} no longer running. Taking over lock.")
        except (ValueError, OSError) as e:
            logger.warning(f"Lock file check failed: {e}. Continuing...")

    # Create lock file with our PID
    my_pid = os.getpid()
    logger.info(f"Creating lock file with PID: {my_pid}")
    with open(LOCK_FILE, 'w') as f:
        f.write(str(my_pid))

    # Clean up on exit
    def cleanup_lock():
        if os.path.exists(LOCK_FILE):
            logger.info("Cleaning up lock file")
            os.unlink(LOCK_FILE)
    atexit.register(cleanup_lock)


# Lazy load the model to show startup message first
model = None

# Configuration - load from config.py if available
try:
    from config import HOTKEY, MODEL_SIZE, DEVICE, COMPUTE_TYPE, AUDIO_DEVICE
    logger.info(f"Loaded config: HOTKEY={HOTKEY}, MODEL={MODEL_SIZE}, DEVICE={DEVICE}")
except ImportError:
    logger.warning("config.py not found, using defaults")
    HOTKEY = 'alt+f'
    MODEL_SIZE = 'small'
    DEVICE = 'cuda'
    COMPUTE_TYPE = 'float16'
    AUDIO_DEVICE = None

SAMPLE_RATE = 16000

# Parse hotkey into individual keys for release detection
HOTKEY_PARTS = [k.strip() for k in HOTKEY.lower().split('+')]

# Recording state
is_recording = False
audio_queue = queue.Queue()
recorded_frames = []


def load_model():
    """Load Whisper model on GPU."""
    global model
    if model is None:
        print(f"Loading {MODEL_SIZE} model on {DEVICE}...")
        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        print("Model loaded. Ready for dictation.")
        device_idx = AUDIO_DEVICE if AUDIO_DEVICE is not None else sd.default.device[0]
        device_name = sd.query_devices(device_idx)['name']
        print(f"Audio input: {device_name}")
        print(f"\nHold [{HOTKEY.upper()}] to record, release to transcribe.\n")
        print("Close this window to exit.\n")
    return model


def audio_callback(indata, frames, time_info, status):
    """Called for each audio block during recording."""
    if status:
        print(f"Audio status: {status}", file=sys.stderr)
    if is_recording:
        recorded_frames.append(indata.copy())


def start_recording():
    """Start recording audio."""
    global is_recording, recorded_frames
    recorded_frames = []
    is_recording = True
    print("🎤 Recording...", end='', flush=True)


def stop_recording_and_transcribe():
    """Stop recording, transcribe, and type the result."""
    global is_recording
    is_recording = False

    if not recorded_frames:
        print(" (no audio captured)")
        return

    print(" processing...", end='', flush=True)

    # Combine recorded audio
    audio_data = np.concatenate(recorded_frames, axis=0)

    # Save to temp file (faster-whisper needs a file)
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_path = f.name
        sf.write(temp_path, audio_data, SAMPLE_RATE)

    try:
        # Transcribe
        start_time = time.time()
        segments, info = model.transcribe(temp_path, beam_size=5, language='en')

        # Collect text
        text = ' '.join(segment.text for segment in segments).strip()
        elapsed = time.time() - start_time

        if text:
            print(f" ({elapsed:.1f}s)")
            print(f"📝 {text}")

            # Copy to clipboard
            pyperclip.copy(text)

            # Type the text into active window
            # Small delay to ensure window focus
            time.sleep(0.05)
            # Add delay between keystrokes to prevent Claude Code crash
            # (Known bug: rapid text injection causes TUI crash)
            keyboard.write(text, delay=0.01)  # 10ms between characters
        else:
            print(" (no speech detected)")

    except Exception as e:
        print(f" Error: {e}")
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except:
            pass


def on_hotkey_press():
    """Called when hotkey is pressed."""
    if not is_recording:
        start_recording()


def on_hotkey_release():
    """Called when hotkey is released."""
    if is_recording:
        stop_recording_and_transcribe()


def main():
    try:
        # Ensure only one instance runs
        check_single_instance()

        logger.info("Starting main()")
        print("=" * 50)
        print("  Voice Dictation Tool (faster-whisper + CUDA)")
        print("=" * 50)
        print()

        # Check microphone before anything else
        logger.info("Checking for microphone...")
        check_microphone()

        # Load model
        logger.info("Loading Whisper model...")
        load_model()
        logger.info("Model loaded successfully")

        # Start audio stream
        logger.info(f"Opening audio stream on device {AUDIO_DEVICE}...")
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=audio_callback,
            blocksize=1024,
            device=AUDIO_DEVICE
        )
        logger.info("Audio stream created")

        with stream:
            logger.info("Audio stream started")
            # Register hotkey
            logger.info(f"Registering hotkey: {HOTKEY}")
            keyboard.add_hotkey(HOTKEY, on_hotkey_press, suppress=True, trigger_on_release=False)
            logger.info("Hotkey registered. Ready for dictation!")

            # Monitor for release
            def check_release():
                global is_recording
                was_pressed = False
                while True:
                    currently_pressed = all(keyboard.is_pressed(key) for key in HOTKEY_PARTS)

                    if was_pressed and not currently_pressed and is_recording:
                        stop_recording_and_transcribe()

                    was_pressed = currently_pressed
                    time.sleep(0.01)  # 10ms polling

            release_thread = threading.Thread(target=check_release, daemon=True)
            release_thread.start()
            logger.info("Release monitor thread started")

            # Keep running
            try:
                keyboard.wait()
            except KeyboardInterrupt:
                logger.info("Received KeyboardInterrupt, exiting...")
                print("\nExiting...")

    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        print(f"Error: {e}")
        input("Press Enter to exit...")
        raise


if __name__ == '__main__':
    logger.info("Script entry point")
    main()
