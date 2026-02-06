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

try:
    import noisereduce as nr
    logger.info("noisereduce imported OK")
    NOISEREDUCE_AVAILABLE = True
except Exception as e:
    logger.warning(f"noisereduce not available: {e}")
    NOISEREDUCE_AVAILABLE = False

# Single instance lock file
LOCK_FILE = os.path.join(tempfile.gettempdir(), 'voice-dictation.lock')

# Active microphone name (set by check_microphone)
active_mic_name = None

# Active audio stream (managed manually for hot-swap device switching)
audio_stream = None

# Lock to prevent concurrent device switches
_switch_lock = threading.Lock()

def check_microphone():
    """Check that a microphone is available."""
    global active_mic_name
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

        # Store device name for tray menu display
        active_mic_name = device_info['name']
        logger.info(f"Will use input device: {active_mic_name}")
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


def get_input_devices():
    """Return list of (index, name) tuples for all input devices."""
    devices = sd.query_devices()
    result = []
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            result.append((i, d['name']))
    return result


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


def on_tray_calibrate(icon, item):
    """Launch noise gate calibration tool."""
    import subprocess
    logger.info("Launching calibration tool...")

    # Get path to calibrate.py
    script_dir = os.path.dirname(os.path.abspath(__file__))
    calibrate_script = os.path.join(script_dir, 'calibrate.py')

    # sys.executable may be pythonw.exe (windowless) which suppresses console
    # windows entirely. Use python.exe instead so the calibration console appears.
    python_exe = sys.executable.replace('pythonw.exe', 'python.exe')

    logger.info(f"Calibrate script: {calibrate_script}")
    logger.info(f"Python executable: {python_exe}")

    # Launch in new console window
    try:
        subprocess.Popen(
            [python_exe, calibrate_script],
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        logger.info("Calibration process launched")
    except Exception as e:
        logger.error(f"Failed to launch calibration: {e}")


def save_audio_device_to_config(device_index):
    """Persist AUDIO_DEVICE to config.py using regex replacement."""
    import re
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')

    if not os.path.exists(config_path):
        logger.warning(f"config.py not found at {config_path}, cannot persist device selection")
        return

    with open(config_path, 'r') as f:
        content = f.read()

    if 'AUDIO_DEVICE' in content:
        content = re.sub(
            r'AUDIO_DEVICE\s*=\s*\S+',
            f'AUDIO_DEVICE = {device_index}',
            content
        )
    else:
        content += f"\n# Audio device (selected from tray menu)\nAUDIO_DEVICE = {device_index}\n"

    with open(config_path, 'w') as f:
        f.write(content)
    logger.info(f"Saved AUDIO_DEVICE = {device_index} to config.py")


def switch_audio_device(device_index, device_name):
    """Switch the audio input to a different device. Hot-swaps the stream."""
    global AUDIO_DEVICE, audio_stream, active_mic_name

    if is_recording:
        logger.warning("Cannot switch microphone while recording")
        return

    if not _switch_lock.acquire(blocking=False):
        logger.warning("Device switch already in progress")
        return

    try:
        logger.info(f"Switching audio device to: [{device_index}] {device_name}")

        # Stop and close current stream
        if audio_stream is not None:
            audio_stream.stop()
            audio_stream.close()
            logger.info("Old audio stream closed")

        # Update global state
        AUDIO_DEVICE = device_index
        active_mic_name = device_name

        # Create and start new stream
        audio_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=audio_callback,
            blocksize=1024,
            device=AUDIO_DEVICE
        )
        audio_stream.start()
        logger.info(f"New audio stream started on: {device_name}")

        # Persist to config.py
        save_audio_device_to_config(device_index)

        # Refresh tray menu checkmarks
        if tray_icon:
            tray_icon.update_menu()

    except Exception as e:
        logger.error(f"Failed to switch audio device: {e}")
    finally:
        _switch_lock.release()


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

# Optional config: custom vocabulary for better recognition
try:
    from config import VOCABULARY
    if VOCABULARY:
        logger.info(f"Custom vocabulary: {VOCABULARY}")
except ImportError:
    VOCABULARY = ''

# Optional config: noise reduction (default off)
try:
    from config import NOISE_REDUCTION
except ImportError:
    NOISE_REDUCTION = False

if NOISE_REDUCTION and not NOISEREDUCE_AVAILABLE:
    logger.warning("NOISE_REDUCTION enabled but noisereduce not installed. Disabling.")
    NOISE_REDUCTION = False
elif NOISE_REDUCTION:
    logger.info("Noise reduction enabled")

# Optional config: clipboard copy (default on)
try:
    from config import USE_CLIPBOARD
except ImportError:
    USE_CLIPBOARD = True

if USE_CLIPBOARD:
    logger.info("Clipboard copy enabled")

# Optional config: noise gate threshold (minimum RMS level to process audio)
try:
    from config import NOISE_GATE_THRESHOLD
except ImportError:
    NOISE_GATE_THRESHOLD = 0.01

if NOISE_GATE_THRESHOLD > 0:
    logger.info(f"Noise gate enabled (threshold={NOISE_GATE_THRESHOLD})")

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

    # Check noise gate threshold
    if NOISE_GATE_THRESHOLD > 0:
        rms = np.sqrt(np.mean(audio_data ** 2))
        if rms < NOISE_GATE_THRESHOLD:
            logger.info(f"Audio too quiet (RMS={rms:.4f} < {NOISE_GATE_THRESHOLD}), skipping")
            update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
            return

    # Apply noise reduction if enabled
    if NOISE_REDUCTION:
        logger.debug("Applying noise reduction...")
        # Flatten to 1D for noisereduce, then reshape back
        audio_flat = audio_data.flatten()
        audio_flat = nr.reduce_noise(y=audio_flat, sr=SAMPLE_RATE)
        audio_data = audio_flat.reshape(-1, 1)

    # Save to temp file (faster-whisper needs a file)
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_path = f.name
        sf.write(temp_path, audio_data, SAMPLE_RATE)

    try:
        # Transcribe with custom vocabulary as initial prompt
        start_time = time.time()
        transcribe_opts = {
            'beam_size': 5,
            'language': TRANSCRIBE_LANGUAGE,
        }
        if VOCABULARY:
            transcribe_opts['initial_prompt'] = VOCABULARY
        segments, info = model.transcribe(temp_path, **transcribe_opts)

        # Collect text
        text = ' '.join(segment.text for segment in segments).strip()
        elapsed = time.time() - start_time

        if text:
            logger.info(f"Transcribed ({elapsed:.1f}s): {text[:50]}...")

            # Copy to clipboard if enabled
            if USE_CLIPBOARD:
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


def build_tray_menu():
    """Build the system tray menu with dynamic microphone submenu."""
    noise_status = 'On' if NOISE_REDUCTION else 'Off'

    # Build microphone submenu items
    input_devices = get_input_devices()

    def make_mic_callback(dev_idx, dev_name):
        """Create a closure for each device menu item."""
        def callback(icon, item):
            switch_audio_device(dev_idx, dev_name)
        return callback

    def make_mic_checked(dev_idx):
        """Create a checked-state closure for each device menu item."""
        def is_checked(item):
            effective = AUDIO_DEVICE if AUDIO_DEVICE is not None else sd.default.device[0]
            return dev_idx == effective
        return is_checked

    mic_items = []
    for dev_idx, dev_name in input_devices:
        display_name = dev_name if len(dev_name) <= 40 else dev_name[:37] + '...'
        mic_items.append(
            pystray.MenuItem(
                display_name,
                make_mic_callback(dev_idx, dev_name),
                checked=make_mic_checked(dev_idx),
                radio=True
            )
        )

    mic_submenu = pystray.Menu(*mic_items) if mic_items else pystray.Menu(
        pystray.MenuItem('No input devices found', lambda: None, enabled=False)
    )

    return pystray.Menu(
        pystray.MenuItem('Select Microphone', mic_submenu),
        pystray.MenuItem(f'Hotkey: {HOTKEY.upper()}', lambda: None, enabled=False),
        pystray.MenuItem(f'Model: {MODEL_SIZE}', lambda: None, enabled=False),
        pystray.MenuItem(f'Language: {LANGUAGE}', lambda: None, enabled=False),
        pystray.MenuItem(f'Noise Reduction: {noise_status}', lambda: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Calibrate Noise Gate...', on_tray_calibrate),
        pystray.MenuItem('Exit', on_tray_exit)
    )


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
    global tray_icon, audio_stream

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

        # Start audio stream (explicit lifecycle for hot-swap device switching)
        logger.info(f"Opening audio stream on device {AUDIO_DEVICE}...")
        audio_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=audio_callback,
            blocksize=1024,
            device=AUDIO_DEVICE
        )
        audio_stream.start()
        logger.info("Audio stream started")

        try:
            if TRAY_AVAILABLE:
                menu = build_tray_menu()

                tray_icon = pystray.Icon(
                    'voice-dictation',
                    create_tray_image('gray'),
                    'Voice Dictation - Loading...',
                    menu
                )

                # Run dictation loop in background thread
                dictation_thread = threading.Thread(
                    target=lambda: run_dictation_loop(audio_stream),
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
                print(f"\n  Hotkey:     [{HOTKEY.upper()}] (hold to record)")
                print(f"  Mic:        {active_mic_name}")
                print(f"  Model:      {MODEL_SIZE}")
                if NOISE_GATE_THRESHOLD > 0:
                    print(f"  Noise Gate: {NOISE_GATE_THRESHOLD} (run calibrate.py to adjust)")
                else:
                    print(f"  Noise Gate: Disabled")
                print("\nClose this window to exit.\n")
                run_dictation_loop(audio_stream)
        finally:
            if audio_stream is not None:
                audio_stream.stop()
                audio_stream.close()
                logger.info("Audio stream closed")

    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        if not TRAY_AVAILABLE:
            print(f"Error: {e}")
            input("Press Enter to exit...")
        raise


if __name__ == '__main__':
    logger.info("Script entry point")
    main()
