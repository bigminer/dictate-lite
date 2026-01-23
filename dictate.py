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

try:
    import pystray
    from PIL import Image, ImageDraw
    logger.info("pystray imported OK")
    TRAY_AVAILABLE = True
except Exception as e:
    logger.warning(f"pystray not available, will use console mode: {e}")
    TRAY_AVAILABLE = False

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

# Global tray icon reference
tray_icon = None


def create_tray_image(color='green'):
    """Create a simple colored circle icon for the system tray."""
    size = 64
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    colors = {
        'green': (34, 197, 94),    # Ready/idle
        'red': (239, 68, 68),      # Recording
        'yellow': (234, 179, 8),   # Processing
        'gray': (156, 163, 175),   # Disabled/loading
    }
    fill_color = colors.get(color, colors['green'])

    # Draw filled circle
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=fill_color)

    return image


def update_tray_icon(color, title=None):
    """Update the tray icon color and tooltip."""
    global tray_icon
    if tray_icon and TRAY_AVAILABLE:
        tray_icon.icon = create_tray_image(color)
        if title:
            tray_icon.title = title


def on_tray_exit(icon, item):
    """Handle exit from tray menu."""
    logger.info("Exit requested from tray menu")
    icon.stop()
    os._exit(0)

# Configuration - load from config.py if available
try:
    from config import HOTKEY, MODEL_SIZE, DEVICE, COMPUTE_TYPE, AUDIO_DEVICE, LANGUAGE
    logger.info(f"Loaded config: HOTKEY={HOTKEY}, MODEL={MODEL_SIZE}, DEVICE={DEVICE}, LANGUAGE={LANGUAGE}")
except ImportError:
    logger.warning("config.py not found, using defaults")
    HOTKEY = 'alt+f'
    MODEL_SIZE = 'small'
    DEVICE = 'cuda'
    COMPUTE_TYPE = 'float16'
    AUDIO_DEVICE = None
    LANGUAGE = 'en'

# Handle 'auto' language setting
TRANSCRIBE_LANGUAGE = None if LANGUAGE == 'auto' else LANGUAGE

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
        logger.info(f"Loading {MODEL_SIZE} model on {DEVICE}...")
        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        logger.info("Model loaded successfully")
        device_idx = AUDIO_DEVICE if AUDIO_DEVICE is not None else sd.default.device[0]
        device_name = sd.query_devices(device_idx)['name']
        logger.info(f"Audio input: {device_name}")
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
    update_tray_icon('red', 'Voice Dictation - Recording...')
    logger.info("Recording started")


def stop_recording_and_transcribe():
    """Stop recording, transcribe, and type the result."""
    global is_recording
    is_recording = False

    if not recorded_frames:
        logger.info("No audio captured")
        update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
        return

    update_tray_icon('yellow', 'Voice Dictation - Processing...')
    logger.info("Processing audio...")

    # Combine recorded audio
    audio_data = np.concatenate(recorded_frames, axis=0)

    # Save to temp file (faster-whisper needs a file)
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_path = f.name
        sf.write(temp_path, audio_data, SAMPLE_RATE)

    try:
        # Transcribe
        start_time = time.time()
        segments, info = model.transcribe(temp_path, beam_size=5, language=TRANSCRIBE_LANGUAGE)

        # Collect text
        text = ' '.join(segment.text for segment in segments).strip()
        elapsed = time.time() - start_time

        if text:
            logger.info(f"Transcribed ({elapsed:.1f}s): {text[:50]}...")

            # Copy to clipboard
            pyperclip.copy(text)

            # Type the text into active window
            # Small delay to ensure window focus
            time.sleep(0.05)
            # Add delay between keystrokes to prevent Claude Code crash
            # (Known bug: rapid text injection causes TUI crash)
            keyboard.write(text, delay=0.01)  # 10ms between characters
        else:
            logger.info("No speech detected")

    except Exception as e:
        logger.error(f"Transcription error: {e}")
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except:
            pass
        # Reset tray icon to ready state
        update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')


def on_hotkey_press():
    """Called when hotkey is pressed."""
    if not is_recording:
        start_recording()


def on_hotkey_release():
    """Called when hotkey is released."""
    if is_recording:
        stop_recording_and_transcribe()


def run_dictation_loop(stream):
    """Run the main dictation loop (hotkey monitoring)."""
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

    # Update tray to ready state
    update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')

    # Keep running until interrupted
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, exiting...")


def main():
    global tray_icon

    try:
        # Ensure only one instance runs
        check_single_instance()

        logger.info("Starting main()")

        # Check microphone before anything else
        logger.info("Checking for microphone...")
        check_microphone()

        # Show loading state in tray
        if TRAY_AVAILABLE:
            update_tray_icon('gray', 'Voice Dictation - Loading model...')

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
            if TRAY_AVAILABLE:
                # Create system tray icon
                menu = pystray.Menu(
                    pystray.MenuItem(f'Hotkey: {HOTKEY.upper()}', lambda: None, enabled=False),
                    pystray.MenuItem(f'Model: {MODEL_SIZE}', lambda: None, enabled=False),
                    pystray.MenuItem(f'Language: {LANGUAGE}', lambda: None, enabled=False),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem('Exit', on_tray_exit)
                )

                tray_icon = pystray.Icon(
                    'voice-dictation',
                    create_tray_image('gray'),
                    'Voice Dictation - Loading...',
                    menu
                )

                # Run dictation loop in background thread
                dictation_thread = threading.Thread(
                    target=lambda: run_dictation_loop(stream),
                    daemon=True
                )
                dictation_thread.start()

                # Run tray icon (blocks until exit)
                logger.info("Starting system tray icon")
                tray_icon.run()
            else:
                # Fallback to console mode
                print("=" * 50)
                print("  Voice Dictation Tool (faster-whisper)")
                print("=" * 50)
                print(f"\nHold [{HOTKEY.upper()}] to record, release to transcribe.")
                print("Close this window to exit.\n")
                run_dictation_loop(stream)

    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        if not TRAY_AVAILABLE:
            print(f"Error: {e}")
            input("Press Enter to exit...")
        raise


if __name__ == '__main__':
    logger.info("Script entry point")
    main()
