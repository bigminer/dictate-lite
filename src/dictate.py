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
import signal
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

def _resolve_device_name_to_index(device_name, input_devices):
    """Resolve a device name string to a device index.
    Tries exact match first, then substring match.
    Returns (index, full_name) or (None, None) if not found.
    """
    for idx, dev in input_devices:
        if dev['name'] == device_name:
            return idx, dev['name']
    for idx, dev in input_devices:
        if device_name in dev['name'] or dev['name'] in device_name:
            logger.info(f"Matched device by substring: '{device_name}' -> [{idx}] '{dev['name']}'")
            return idx, dev['name']
    return None, None


def check_microphone():
    """Check microphone with fallback: saved device -> system default -> first available.
    Returns True if a usable mic was found, False otherwise.
    Updates AUDIO_DEVICE and active_mic_name globals.

    AUDIO_DEVICE can be:
      - None: use system default
      - str:  device name to resolve (current format)
      - int:  legacy device index (deprecated, will be migrated to name)
    """
    global active_mic_name, AUDIO_DEVICE
    try:
        devices = sd.query_devices()
        input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
        if not input_devices:
            logger.error("No input devices found on this system")
            return False

        logger.info(f"Found {len(input_devices)} input device(s)")
        for i, d in input_devices:
            logger.debug(f"  [{i}] {d['name']}")

        # Try saved device from config first
        if AUDIO_DEVICE is not None:
            if isinstance(AUDIO_DEVICE, str):
                resolved_idx, resolved_name = _resolve_device_name_to_index(AUDIO_DEVICE, input_devices)
                if resolved_idx is not None:
                    active_mic_name = resolved_name
                    AUDIO_DEVICE = resolved_name
                    logger.info(f"Using saved device by name: [{resolved_idx}] {active_mic_name}")
                    return True
                else:
                    logger.warning(f"Saved device name '{AUDIO_DEVICE}' not found, falling back to default")
            elif isinstance(AUDIO_DEVICE, int):
                logger.warning(f"AUDIO_DEVICE is an integer ({AUDIO_DEVICE}). Integer indices are deprecated.")
                try:
                    device_info = sd.query_devices(AUDIO_DEVICE)
                    if device_info['max_input_channels'] > 0:
                        active_mic_name = device_info['name']
                        AUDIO_DEVICE = active_mic_name
                        save_audio_device_to_config(active_mic_name)
                        logger.info(f"Migrated legacy index to name: '{active_mic_name}'")
                        return True
                    else:
                        logger.warning(f"Legacy device [{AUDIO_DEVICE}] has no input channels, falling back")
                except Exception as e:
                    logger.warning(f"Legacy device index [{AUDIO_DEVICE}] unavailable ({e}), falling back")
            AUDIO_DEVICE = None

        # Fall back to system default
        default_idx = sd.default.device[0]
        if default_idx is not None and default_idx >= 0:
            try:
                device_info = sd.query_devices(default_idx)
                if device_info['max_input_channels'] > 0:
                    active_mic_name = device_info['name']
                    AUDIO_DEVICE = active_mic_name
                    save_audio_device_to_config(active_mic_name)
                    logger.info(f"Using system default device: [{default_idx}] {active_mic_name} (persisted)")
                    return True
            except Exception as e:
                logger.warning(f"System default device [{default_idx}] failed: {e}")

        # Last resort: first available input device
        first_idx, first_dev = input_devices[0]
        active_mic_name = first_dev['name']
        AUDIO_DEVICE = active_mic_name
        save_audio_device_to_config(active_mic_name)
        logger.info(f"Falling back to first available input: [{first_idx}] {active_mic_name} (persisted)")
        return True

    except Exception as e:
        logger.exception(f"Error enumerating audio devices: {e}")
        return False


def get_input_devices():
    """Return list of (index, name) tuples for all input devices."""
    devices = sd.query_devices()
    result = []
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            result.append((i, d['name']))
    return result


def _is_python_process(pid):
    """Check if the given PID is a python.exe or pythonw.exe process via ctypes."""
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32
    # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False

    try:
        # QueryFullProcessImageNameW to get the executable path
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        # dwFlags=0 means Win32 path format
        result = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not result:
            logger.debug(f"QueryFullProcessImageNameW failed for PID {pid}")
            return False

        exe_path = buf.value.lower()
        exe_name = os.path.basename(exe_path)
        logger.debug(f"PID {pid} executable: {exe_name} ({exe_path})")
        return exe_name in ('python.exe', 'pythonw.exe')
    finally:
        kernel32.CloseHandle(handle)


def check_single_instance():
    """Ensure only one instance runs. Exit silently if already running."""
    logger.info(f"Checking single instance. Lock file: {LOCK_FILE}")
    if os.path.exists(LOCK_FILE):
        try:
            # Check if lock file is stale (older than 24 hours)
            lock_age_seconds = time.time() - os.path.getmtime(LOCK_FILE)
            if lock_age_seconds > 86400:  # 24 hours
                logger.info(f"Lock file is {lock_age_seconds / 3600:.1f} hours old - treating as stale")
            else:
                with open(LOCK_FILE, 'r') as f:
                    pid = int(f.read().strip())
                logger.info(f"Found existing lock file with PID: {pid}")

                # Check if process is still running AND is actually a Python process
                if _is_python_process(pid):
                    # Process exists and is Python, exit silently
                    logger.info(f"Process {pid} is still running (confirmed Python). Exiting.")
                    sys.exit(0)
                else:
                    logger.info(f"Process {pid} is not running or is not Python. Taking over lock.")
        except (ValueError, OSError) as e:
            logger.warning(f"Lock file check failed: {e}. Continuing...")

    # Create lock file with our PID
    my_pid = os.getpid()
    logger.info(f"Creating lock file with PID: {my_pid}")
    with open(LOCK_FILE, 'w') as f:
        f.write(str(my_pid))

    # Clean up on exit
    def cleanup_lock():
        try:
            if os.path.exists(LOCK_FILE):
                logger.info("Cleaning up lock file")
                os.unlink(LOCK_FILE)
        except Exception as e:
            logger.warning(f"Error in cleanup_lock: {e}")
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


def cleanup_resources():
    """Gracefully release all resources before exit.
    Closes audio stream, unhooks keyboard, removes lock file,
    then calls os._exit(0) as last resort (pystray/keyboard can hang with sys.exit).
    """
    logger.info("cleanup_resources() called - shutting down gracefully")

    # Close audio stream
    try:
        if audio_stream is not None:
            audio_stream.stop()
            audio_stream.close()
            logger.info("Audio stream closed")
    except Exception as e:
        logger.warning(f"Error closing audio stream: {e}")

    # Unhook keyboard hotkeys
    try:
        keyboard.unhook_all()
        logger.info("Keyboard hooks removed")
    except Exception as e:
        logger.warning(f"Error unhooking keyboard: {e}")

    # Remove lock file
    try:
        if os.path.exists(LOCK_FILE):
            os.unlink(LOCK_FILE)
            logger.info("Lock file removed")
    except Exception as e:
        logger.warning(f"Error removing lock file: {e}")

    # os._exit as last step - required because pystray/keyboard threads
    # can prevent a clean sys.exit()
    os._exit(0)


def on_tray_exit(icon, item):
    """Handle exit from tray menu."""
    logger.info("Exit requested from tray menu")
    icon.stop()
    cleanup_resources()


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


def save_audio_device_to_config(device_name):
    """Persist AUDIO_DEVICE to config.py as a device name string (or None)."""
    import re
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')

    if not os.path.exists(config_path):
        logger.warning(f"config.py not found at {config_path}, cannot persist device selection")
        return

    with open(config_path, 'r') as f:
        content = f.read()

    # Format the value: None stays as None, strings get quoted
    if device_name is None:
        value_str = 'None'
    else:
        escaped = str(device_name).replace("'", "\\'")
        value_str = f"'{escaped}'"

    if 'AUDIO_DEVICE' in content:
        # Match AUDIO_DEVICE = <anything to end of line>
        content = re.sub(
            r"AUDIO_DEVICE\s*=\s*.*",
            f"AUDIO_DEVICE = {value_str}",
            content
        )
    else:
        content += f"\n# Audio device (selected from tray menu)\nAUDIO_DEVICE = {value_str}\n"

    with open(config_path, 'w') as f:
        f.write(content)
    logger.info(f"Saved AUDIO_DEVICE = {value_str} to config.py")


def switch_audio_device(device_index, device_name):
    """Switch the audio input to a different device. Hot-swaps the stream.

    Opens the new stream BEFORE closing the old one. If the new stream fails,
    the old stream is kept running. Global state is only updated on success.
    Device is persisted by name, not index.
    """
    global AUDIO_DEVICE, audio_stream, active_mic_name

    if is_recording:
        logger.warning("Cannot switch microphone while recording")
        return

    if not _switch_lock.acquire(blocking=False):
        logger.warning("Device switch already in progress")
        return

    try:
        logger.info(f"Switching audio device to: [{device_index}] {device_name}")

        # Try to open the new stream BEFORE touching the old one
        try:
            new_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                callback=audio_callback,
                blocksize=1024,
                device=device_index
            )
            new_stream.start()
            logger.info(f"New audio stream opened on: [{device_index}] {device_name}")
        except Exception as e:
            logger.error(f"Failed to open new stream on [{device_index}] {device_name}: {e}")
            logger.info("Keeping current audio device unchanged")
            return

        # New stream is confirmed working - now close the old one
        old_stream = audio_stream
        if old_stream is not None:
            try:
                old_stream.stop()
                old_stream.close()
                logger.info("Old audio stream closed")
            except Exception as e:
                logger.warning(f"Error closing old stream (non-fatal): {e}")

        # Update global state only after new stream is confirmed working
        audio_stream = new_stream
        AUDIO_DEVICE = device_name  # store name, not index
        active_mic_name = device_name

        # Persist device name to config.py
        save_audio_device_to_config(device_name)

        # Update tray to ready state (handles recovery from error state)
        if model is not None:
            update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')

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
recording_start_time = 0

# Microphone health monitoring
last_callback_time = 0
MAX_RECORDING_SECONDS = 120
_silence_flag = False  # Set by audio_callback, read by monitor thread


def load_model():
    """Load Whisper model on GPU."""
    global model
    if model is None:
        logger.info(f"Loading {MODEL_SIZE} model on {DEVICE}...")
        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        logger.info("Model loaded successfully")
    return model


def audio_callback(indata, frames, time_info, status):
    """Called for each audio block during recording."""
    global last_callback_time, _silence_flag
    last_callback_time = time.time()
    if status:
        logger.warning(f"Audio status: {status}")
    if is_recording:
        recorded_frames.append(indata.copy())
        # Lightweight silence detection: check if this block is near-silent
        # and set a flag for the monitor thread to act on (avoid heavy work in callback)
        rms = np.sqrt(np.mean(indata ** 2))
        if rms < 1e-6:
            _silence_flag = True
        else:
            _silence_flag = False


def start_recording():
    """Start recording audio."""
    global is_recording, recorded_frames, recording_start_time, _silence_flag
    recorded_frames = []
    _silence_flag = False
    recording_start_time = time.time()
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

    # Combine recorded audio with corruption guard
    try:
        audio_data = np.concatenate(recorded_frames, axis=0)
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to concatenate audio frames (corrupt data?): {e}")
        update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
        return

    # Check minimum duration (< 0.1s is too short to transcribe)
    duration_s = len(audio_data) / SAMPLE_RATE
    if duration_s < 0.1:
        logger.info(f"Audio too short ({duration_s:.3f}s < 0.1s), skipping transcription")
        update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
        return

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


def _dynamic_mic_submenu():
    """Generate the microphone submenu items dynamically each time the menu is opened.
    This ensures newly connected/disconnected devices appear without restarting.
    """
    try:
        input_devices = get_input_devices()
    except Exception as e:
        logger.warning(f"Failed to enumerate devices for tray menu: {e}")
        return [pystray.MenuItem('Error listing devices', lambda: None, enabled=False)]

    if not input_devices:
        return [pystray.MenuItem('No input devices found', lambda: None, enabled=False)]

    def make_mic_callback(dev_idx, dev_name):
        def callback(icon, item):
            switch_audio_device(dev_idx, dev_name)
        return callback

    def make_mic_checked(dev_name):
        def is_checked(item):
            return active_mic_name == dev_name
        return is_checked

    items = []
    for dev_idx, dev_name in input_devices:
        display_name = dev_name if len(dev_name) <= 40 else dev_name[:37] + '...'
        items.append(
            pystray.MenuItem(
                display_name,
                make_mic_callback(dev_idx, dev_name),
                checked=make_mic_checked(dev_name),
                radio=True
            )
        )
    return items


def build_tray_menu():
    """Build the system tray menu with dynamic microphone submenu.
    The microphone list is rebuilt each time the menu is opened via a callable submenu.
    """
    noise_status = 'On' if NOISE_REDUCTION else 'Off'

    mic_submenu = pystray.Menu(_dynamic_mic_submenu)

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


def run_dictation_loop():
    """Run the main dictation loop (hotkey monitoring)."""
    # Register hotkey
    logger.info(f"Registering hotkey: {HOTKEY}")
    keyboard.add_hotkey(HOTKEY, on_hotkey_press, suppress=True, trigger_on_release=False)
    logger.info("Hotkey registered. Ready for dictation!")

    # Monitor for release and recording timeout
    def check_release():
        global is_recording
        was_pressed = False
        silence_warned = False
        while True:
            currently_pressed = all(keyboard.is_pressed(key) for key in HOTKEY_PARTS)

            if was_pressed and not currently_pressed and is_recording:
                silence_warned = False
                stop_recording_and_transcribe()

            # Check recording timeout
            if is_recording and recording_start_time > 0:
                elapsed = time.time() - recording_start_time
                if elapsed > MAX_RECORDING_SECONDS:
                    logger.warning(f"Recording timeout after {MAX_RECORDING_SECONDS}s - force stopping")
                    silence_warned = False
                    stop_recording_and_transcribe()

                # Check silence flag from audio_callback (update tray warning)
                if _silence_flag and not silence_warned:
                    silence_warned = True
                    update_tray_icon('red', 'Recording - Warning: mic may be muted')
                elif not _silence_flag and silence_warned:
                    silence_warned = False
                    update_tray_icon('red', 'Voice Dictation - Recording...')

            was_pressed = currently_pressed
            time.sleep(0.01)  # 10ms polling

    release_thread = threading.Thread(target=check_release, daemon=True)
    release_thread.start()
    logger.info("Release monitor thread started")

    # Keep running until interrupted
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, exiting...")


def test_microphone():
    """Non-blocking microphone test: record 0.5s and check if audio level is reasonable.
    Warns via log and tray tooltip if the mic appears muted, but never blocks startup.
    Must be called after audio_stream is started.
    """
    if audio_stream is None or not audio_stream.active:
        logger.warning("test_microphone: audio stream not active, skipping test")
        return

    logger.info("Running microphone self-test (0.5s capture)...")
    test_frames = []

    def _test_cb(indata, frames, time_info, status):
        test_frames.append(indata.copy())

    try:
        # Use a temporary stream for the test so we don't interfere with
        # the main audio_callback (which only records when is_recording=True)
        test_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=_test_cb,
            blocksize=1024,
            device=AUDIO_DEVICE
        )
        test_stream.start()
        time.sleep(0.5)
        test_stream.stop()
        test_stream.close()
    except Exception as e:
        logger.warning(f"Microphone self-test failed to capture audio: {e}")
        return

    if not test_frames:
        logger.warning("Microphone self-test: no frames captured")
        return

    try:
        test_audio = np.concatenate(test_frames, axis=0)
        rms = np.sqrt(np.mean(test_audio ** 2))
    except Exception as e:
        logger.warning(f"Microphone self-test: error computing RMS: {e}")
        return

    if rms < 1e-6:
        logger.warning(f"Microphone self-test: RMS={rms:.8f} - mic may be muted or disconnected")
        update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}] (Warning: mic may be muted)')
    else:
        logger.info(f"Microphone self-test passed (RMS={rms:.6f})")


def stream_health_watchdog():
    """Daemon thread that monitors audio stream health.
    Checks every 5 seconds whether the stream is active and receiving callbacks.
    If the stream appears dead, attempts to reopen it.
    """
    global audio_stream, last_callback_time
    logger.info("Stream health watchdog started")

    while True:
        time.sleep(5)

        # Skip checks if we don't have a stream yet or we're recording
        if audio_stream is None or is_recording:
            continue

        stream_active = False
        try:
            stream_active = audio_stream.active
        except Exception:
            pass

        callback_stale = False
        if last_callback_time > 0:
            callback_stale = (time.time() - last_callback_time) > 10

        if not stream_active or callback_stale:
            reason = "stream inactive" if not stream_active else "no callbacks for >10s"
            logger.error(f"Audio stream appears dead ({reason}). Attempting recovery...")
            update_tray_icon('gray', 'Voice Dictation - Audio stream lost')

            try:
                # Close the old stream if it still exists
                try:
                    audio_stream.stop()
                    audio_stream.close()
                except Exception:
                    pass

                # Reopen with current AUDIO_DEVICE
                audio_stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype='float32',
                    callback=audio_callback,
                    blocksize=1024,
                    device=AUDIO_DEVICE
                )
                audio_stream.start()
                last_callback_time = time.time()
                logger.info("Audio stream recovered successfully")
                if model is not None:
                    update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
            except Exception as e:
                logger.error(f"Failed to recover audio stream: {e}")
                update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')


def init_audio_and_dictation():
    """Background initialization: mic check, model load, audio stream, hotkey registration.
    The tray icon is already visible (gray) when this runs.
    """
    global audio_stream
    mic_ok = False

    # Check microphone with fallback chain
    logger.info("Checking for microphone...")
    mic_ok = check_microphone()
    if not mic_ok:
        update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
        logger.error("No usable microphone found. Connect a device and select it from the tray menu.")

    # Load model regardless of mic status (so it's ready when user plugs in a mic)
    try:
        update_tray_icon('gray', 'Voice Dictation - Loading model...')
        logger.info("Loading Whisper model...")
        load_model()
    except Exception as e:
        logger.exception(f"Failed to load Whisper model: {e}")
        update_tray_icon('gray', 'Voice Dictation - Model load failed (see log)')
        # Still run the hotkey loop so the tray stays alive
        run_dictation_loop()
        return

    # Start audio stream if mic was found
    if mic_ok:
        try:
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
            update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')

            # Run non-blocking mic test after stream is confirmed working
            test_microphone()

            # Start stream health watchdog daemon
            watchdog_thread = threading.Thread(target=stream_health_watchdog, daemon=True)
            watchdog_thread.start()
        except Exception as e:
            logger.exception(f"Failed to open audio stream: {e}")
            update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')
    else:
        update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')

    # Register hotkey and block (dictation works if stream is active)
    run_dictation_loop()


def main():
    global tray_icon

    try:
        # Ensure only one instance runs
        check_single_instance()
        logger.info("Starting main()")

        # Register signal handlers for graceful shutdown
        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            logger.info(f"Received signal {sig_name} ({signum}), cleaning up...")
            cleanup_resources()

        signal.signal(signal.SIGTERM, _signal_handler)
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, _signal_handler)
        logger.info("Signal handlers registered (SIGTERM, SIGBREAK)")

        if TRAY_AVAILABLE:
            # Show tray icon immediately so the user sees the app is running
            menu = build_tray_menu()
            tray_icon = pystray.Icon(
                'voice-dictation',
                create_tray_image('gray'),
                'Voice Dictation - Starting...',
                menu
            )

            # Run mic check, model load, and dictation in background thread
            init_thread = threading.Thread(target=init_audio_and_dictation, daemon=True)
            init_thread.start()

            # Run tray icon on main thread (blocks until exit)
            logger.info("Starting system tray icon")
            tray_icon.run()
        else:
            # Console mode - run init synchronously
            print("=" * 50)
            print("  Voice Dictation Tool (faster-whisper)")
            print("=" * 50)
            init_audio_and_dictation()

    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        raise


if __name__ == '__main__':
    logger.info("Script entry point")
    main()
