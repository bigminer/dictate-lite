"""
Voice Dictation Tool
Hold Alt+F to record, release to transcribe and type.
Uses faster-whisper with GPU acceleration.
"""

import sys
import threading
import tempfile
import os
import time
import atexit
import signal
import logging
from logging.handlers import RotatingFileHandler

import audio_device_identity as audio_identity
import audio_capture
import config_store
import runtime_state
import transcription_io
from app_state import DictationAppState
from audio_stream_manager import AudioStreamManager

# Set up logging FIRST before any other imports that might fail
LOG_DIR = os.path.join(os.path.expanduser('~'), 'voice-dictation')
LOG_FILE = os.path.join(LOG_DIR, 'dictation.log')
os.makedirs(LOG_DIR, exist_ok=True)

_DEFAULT_LOG_LEVEL_NAME = os.environ.get('VOICE_DICTATION_LOG_LEVEL', 'INFO').upper()
_DEFAULT_LOG_LEVEL = getattr(logging, _DEFAULT_LOG_LEVEL_NAME, logging.INFO)
if not isinstance(_DEFAULT_LOG_LEVEL, int):
    _DEFAULT_LOG_LEVEL = logging.INFO

logging.basicConfig(
    level=_DEFAULT_LOG_LEVEL,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def _apply_noisy_logger_policy(level):
    """Keep chatty dependency loggers from overwhelming dictation logs."""
    if level <= logging.DEBUG:
        return
    for logger_name in (
        'httpx',
        'httpcore',
        'urllib3',
        'filelock',
        'faster_whisper',
        'ctranslate2',
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


_apply_noisy_logger_policy(_DEFAULT_LOG_LEVEL)

logger.info("=" * 50)
logger.info("Voice Dictation starting...")
logger.info(f"Python: {sys.version}")
logger.info(f"Working dir: {os.getcwd()}")
logger.info(f"Log file: {LOG_FILE}")
logger.info(f"State file: {runtime_state.STATE_FILE}")

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
MUTEX_NAME = r'Local\VoiceDictationSingleton'
_instance_mutex_handle = None

STATE = DictationAppState()
stream_manager = None
_TRAY_IMAGE_CACHE = {}

# Lock to prevent concurrent device switches
_switch_lock = threading.Lock()

def _normalize_device_name(name):
    """Normalize device name for stable identity hashing."""
    return audio_identity.normalize_device_name(name)


def _build_device_uid(device_name, hostapi_name, device_info):
    """Build a stable UID from microphone metadata."""
    return audio_identity.build_device_uid(device_name, hostapi_name, device_info)


def _enumerate_input_devices():
    """Return input tuples: (index, name, hostapi_name, hostapi_index, device_uid)."""
    return audio_identity.enumerate_input_devices(sd)


def _choose_candidate(candidates, preferred_index=None, default_index=None):
    """Choose a device tuple from candidates with deterministic preference order."""
    return audio_identity.choose_candidate(
        candidates,
        preferred_index=preferred_index,
        default_index=default_index
    )


def _resolve_device_name_to_index(
    device_name,
    input_devices,
    preferred_hostapi=None,
    preferred_index=None,
    default_index=None
):
    """Resolve saved device identity to an input device tuple.

    Resolution order:
    1) exact name + hostapi
    2) exact name
    3) substring name + hostapi
    4) substring name
    For ambiguous matches, prefer saved index then system default index then WASAPI.
    Returns (index, name, hostapi_name, device_uid) or (None, None, None, None).
    """
    # Keep wrapper for compatibility with existing tests/call sites.
    resolved = audio_identity.resolve_device_name(
        device_name,
        input_devices,
        preferred_hostapi=preferred_hostapi,
        preferred_index=preferred_index,
        default_index=default_index
    )
    idx, name, hostapi_name, uid = resolved
    if idx is not None:
        logger.info(f"Resolved device by name: [{idx}] '{name}' ({hostapi_name}) uid={uid}")
    return resolved


def _resolve_device_uid_to_index(device_uid, input_devices, default_index=None):
    """Resolve saved UID to an input device tuple."""
    return audio_identity.resolve_device_uid(
        device_uid,
        input_devices,
        default_index=default_index
    )


def _get_stream_manager():
    """Lazily create the shared audio stream manager."""
    global stream_manager
    if stream_manager is None:
        stream_manager = AudioStreamManager(
            sd,
            callback=audio_callback,
            sample_rate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            blocksize=1024,
            logger=logger,
        )
    return stream_manager


def check_microphone():
    """Check microphone with fallback: saved device -> system default -> first available.
    Returns True if a usable mic was found, False otherwise.
    Updates AUDIO_DEVICE identity globals and active microphone globals.

    AUDIO_DEVICE can be:
      - None: use system default
      - str:  device name to resolve (current format)
      - int:  legacy device index (deprecated, will be migrated to name)
    """
    global AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID
    STATE.active_mic_name = None
    STATE.active_mic_index = None
    STATE.active_mic_hostapi = None
    try:
        input_devices = _enumerate_input_devices()
        if not input_devices:
            logger.error("No input devices found on this system")
            return False

        logger.info(f"Found {len(input_devices)} input device(s)")
        for idx, name, hostapi_name, _, device_uid in input_devices:
            logger.debug(f"  [{idx}] {name} ({hostapi_name})")
            logger.debug(f"    uid={device_uid}")
        STATE.last_device_topology_signature = _current_input_topology_signature(input_devices)

        prior_name = AUDIO_DEVICE
        prior_hostapi = AUDIO_DEVICE_HOSTAPI
        prior_index = AUDIO_DEVICE_INDEX
        prior_uid = AUDIO_DEVICE_UID

        resolved_idx, resolved_name, resolved_hostapi, resolved_uid = audio_identity.resolve_preferred_input_device(
            sd,
            input_devices,
            saved_name=AUDIO_DEVICE,
            saved_hostapi=AUDIO_DEVICE_HOSTAPI,
            saved_index=AUDIO_DEVICE_INDEX,
            saved_uid=AUDIO_DEVICE_UID
        )

        if resolved_idx is None:
            logger.error("Failed to resolve any usable microphone device")
            AUDIO_DEVICE = None
            AUDIO_DEVICE_HOSTAPI = None
            AUDIO_DEVICE_INDEX = None
            AUDIO_DEVICE_UID = None
            return False

        STATE.active_mic_name = resolved_name
        STATE.active_mic_index = resolved_idx
        STATE.active_mic_hostapi = resolved_hostapi
        AUDIO_DEVICE = resolved_name
        AUDIO_DEVICE_HOSTAPI = resolved_hostapi
        AUDIO_DEVICE_INDEX = resolved_idx
        AUDIO_DEVICE_UID = resolved_uid

        if (
            prior_name != resolved_name
            or prior_hostapi != resolved_hostapi
            or prior_index != resolved_idx
            or prior_uid != resolved_uid
        ):
            save_audio_device_to_config(
                resolved_name,
                resolved_hostapi,
                resolved_idx,
                device_uid=resolved_uid
            )

        logger.info(
            f"Using resolved microphone: [{resolved_idx}] {STATE.active_mic_name} ({resolved_hostapi}) uid={resolved_uid}"
        )
        return True

    except Exception as e:
        logger.exception(f"Error enumerating audio devices: {e}")
        return False


def get_input_devices():
    """Return list of (index, name, hostapi_name, hostapi_index, device_uid) for input devices."""
    return _enumerate_input_devices()


def _get_active_stream_device():
    """Return the best device identifier for opening streams (prefer numeric index)."""
    if STATE.active_mic_index is not None:
        return STATE.active_mic_index
    if isinstance(AUDIO_DEVICE_INDEX, int):
        return AUDIO_DEVICE_INDEX
    return AUDIO_DEVICE


def _current_input_topology_signature(input_devices=None):
    """Return a deterministic signature for currently available input devices."""
    if input_devices is None:
        input_devices = _enumerate_input_devices()
    return audio_identity.current_input_topology_signature(input_devices)


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


def _get_process_command_line(pid):
    """Return command line for PID via PowerShell CIM, or None if unavailable."""
    import subprocess

    ps_cmd = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\"; "
        "if ($p -and $p.CommandLine) { $p.CommandLine }"
    )

    try:
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=2,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        )
    except Exception as e:
        logger.debug(f"Failed to query command line for PID {pid}: {e}")
        return None

    if completed.returncode != 0:
        logger.debug(f"PowerShell CIM query failed for PID {pid}: rc={completed.returncode}")
        return None

    command_line = completed.stdout.strip()
    return command_line if command_line else None


def _is_dictation_command_line(command_line):
    """Return True if a command line appears to launch this dictation script."""
    if not command_line:
        return False

    normalized = command_line.replace('/', '\\').lower()
    script_path = os.path.abspath(__file__).replace('/', '\\').lower()

    return (
        script_path in normalized
        or 'src\\dictate.py' in normalized
        or normalized.endswith('\\dictate.py')
        or normalized.endswith(' dictate.py')
    )


def _is_our_dictation_process(pid):
    """Return True only when PID appears to be this Voice Dictation process."""
    command_line = _get_process_command_line(pid)
    if not command_line:
        return False

    is_match = _is_dictation_command_line(command_line)
    logger.debug(f"PID {pid} command line match={is_match}: {command_line}")
    return is_match


def _acquire_single_instance_mutex():
    """Acquire process-wide singleton mutex. Returns True if this instance owns it."""
    global _instance_mutex_handle
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        logger.warning("CreateMutexW failed; falling back to lock-file only mode")
        return True

    # ERROR_ALREADY_EXISTS == 183
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        return False

    _instance_mutex_handle = handle
    return True


def _release_single_instance_mutex():
    """Release singleton mutex handle if held."""
    global _instance_mutex_handle
    if not _instance_mutex_handle:
        return

    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(_instance_mutex_handle)
    except Exception as e:
        logger.warning(f"Failed to release mutex handle: {e}")
    finally:
        _instance_mutex_handle = None


def check_single_instance():
    """Ensure only one instance runs. Exit silently if already running.

    Primary guard: named OS mutex (survives stale lock files and process-ID reuse).
    Secondary guard: PID lock file for diagnostics and manual recovery visibility.
    """
    logger.info(f"Checking single instance. Mutex: {MUTEX_NAME}")
    if not _acquire_single_instance_mutex():
        logger.info("Another Voice Dictation instance already owns the mutex. Exiting.")
        sys.exit(0)

    logger.info(f"Checking single instance. Lock file: {LOCK_FILE}")
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r', encoding='utf-8', errors='replace') as f:
                prior_pid = f.read().strip()
            logger.info(f"Existing lock file found (PID text: '{prior_pid}'). Overwriting with current PID.")
        except OSError as e:
            logger.warning(f"Could not read existing lock file: {e}. Recreating.")

    # Create/refresh lock file with our PID
    my_pid = os.getpid()
    logger.info(f"Creating lock file with PID: {my_pid}")
    with open(LOCK_FILE, 'w', encoding='utf-8') as f:
        f.write(str(my_pid))

    # Clean up on normal interpreter exit
    def cleanup_lock():
        try:
            if os.path.exists(LOCK_FILE):
                logger.info("Cleaning up lock file")
                os.unlink(LOCK_FILE)
        except Exception as e:
            logger.warning(f"Error in cleanup_lock: {e}")
        finally:
            _release_single_instance_mutex()
    atexit.register(cleanup_lock)


def create_tray_image(color='green'):
    """Create a simple colored circle icon for the system tray."""
    cached_image = _TRAY_IMAGE_CACHE.get(color)
    if cached_image is not None:
        return cached_image

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

    _TRAY_IMAGE_CACHE[color] = image
    return image


def update_tray_icon(color, title=None):
    """Update the tray icon color and tooltip."""
    if STATE.tray_icon and TRAY_AVAILABLE:
        if color != STATE.tray_color:
            STATE.tray_icon.icon = create_tray_image(color)
            STATE.tray_color = color
        if title and title != STATE.tray_title:
            STATE.tray_icon.title = title
            STATE.tray_title = title


def cleanup_resources():
    """Gracefully release all resources before exit.
    Closes audio stream, unhooks keyboard, removes lock file,
    then calls os._exit(0) as last resort (pystray/keyboard can hang with sys.exit).
    """
    logger.info("cleanup_resources() called - shutting down gracefully")

    STATE.shutdown_event.set()

    # Close audio stream
    try:
        manager = _get_stream_manager()
        manager.close()
        STATE.audio_stream = None
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

    # Release singleton mutex
    _release_single_instance_mutex()

    # Persist clean shutdown marker before hard exit
    _write_runtime_state('shutdown_clean', reason='cleanup_resources')

    # Give daemon threads up to 2s to finish before hard exit
    logger.info("Waiting up to 2s for threads to finish...")
    time.sleep(2)

    # os._exit as last step - required because pystray/keyboard threads
    # can prevent a clean sys.exit()
    os._exit(0)


def on_tray_exit(icon, item):
    """Handle exit from tray menu."""
    logger.info("Exit requested from tray menu")
    icon.stop()
    cleanup_resources()


def _launch_script_in_console(script_name, args=None):
    """Launch a src/*.py helper script in a new console window."""
    import subprocess
    args = args or []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, script_name)
    python_exe = sys.executable.replace('pythonw.exe', 'python.exe')
    command = [python_exe, script_path] + list(args)

    try:
        subprocess.Popen(
            command,
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        logger.info(f"Launched helper script: {script_name} {' '.join(args)}")
    except Exception as e:
        logger.error(f"Failed to launch {script_name}: {e}")


def on_tray_calibrate(icon, item):
    """Launch noise gate calibration tool."""
    logger.info("Launching calibration tool...")
    _launch_script_in_console('calibrate.py')


def on_tray_healthcheck(icon, item):
    """Launch startup healthcheck in a separate console window."""
    logger.info("Launching startup healthcheck...")
    _launch_script_in_console('startup_healthcheck.py', ['--healthcheck-only'])


def _write_runtime_state(status, reason=None, details=None):
    """Atomically persist runtime lifecycle state."""
    runtime_state.write_runtime_state(
        status=status,
        reason=reason,
        details=details,
        logger=logger
    )


def save_audio_device_to_config(device_name, device_hostapi=None, device_index=None, device_uid=None):
    """Persist AUDIO_DEVICE identity to config.py using structured key upserts."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
    updates = {
        'AUDIO_DEVICE': device_name,
        'AUDIO_DEVICE_HOSTAPI': device_hostapi,
        'AUDIO_DEVICE_INDEX': device_index,
        'AUDIO_DEVICE_UID': device_uid,
    }
    comments = {
        'AUDIO_DEVICE': '# Saved audio device identity (auto-managed)',
    }
    saved = config_store.update_config_values(config_path, updates, comments=comments, logger=logger)
    if not saved:
        return
    logger.info(
        f"Saved AUDIO_DEVICE identity to config.py: "
        f"name={config_store.format_python_literal(device_name)}, "
        f"hostapi={config_store.format_python_literal(device_hostapi)}, "
        f"index={config_store.format_python_literal(device_index)}, "
        f"uid={config_store.format_python_literal(device_uid)}"
    )


def switch_audio_device(device_index, device_name, device_hostapi=None, device_uid=None):
    """Switch the audio input to a different device. Hot-swaps the stream.

    Opens the new stream BEFORE closing the old one. If the new stream fails,
    the old stream is kept running. Global state is only updated on success.
    Device is persisted by name, not index.
    """
    global AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID

    if _is_audio_pipeline_busy():
        logger.warning("Cannot switch microphone while recording or processing")
        return

    if not _switch_lock.acquire(blocking=False):
        logger.warning("Device switch already in progress")
        return

    try:
        if not device_hostapi or not device_uid:
            try:
                device_entry = next(
                    (d for d in _enumerate_input_devices() if d[0] == device_index),
                    None
                )
                if device_entry is not None:
                    _, _, detected_hostapi, _, detected_uid = device_entry
                    if not device_hostapi:
                        device_hostapi = detected_hostapi
                    if not device_uid:
                        device_uid = detected_uid
            except Exception:
                pass

        if not device_hostapi or not device_uid:
            try:
                dev_info = sd.query_devices(device_index)
                hostapis = sd.query_hostapis()
                hostapi_index = dev_info.get('hostapi')
                if not device_hostapi:
                    device_hostapi = 'Unknown'
                    if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(hostapis):
                        device_hostapi = hostapis[hostapi_index]['name']
                if not device_uid:
                    device_uid = _build_device_uid(device_name, device_hostapi, dev_info)
            except Exception:
                if not device_hostapi:
                    device_hostapi = None
                if not device_uid:
                    device_uid = None

        logger.info(
            f"Switching audio device to: [{device_index}] {device_name} ({device_hostapi}) uid={device_uid}"
        )

        manager = _get_stream_manager()
        try:
            manager.switch(device_index)
            STATE.audio_stream = manager.stream
            logger.info(f"New audio stream opened on: [{device_index}] {device_name}")
        except Exception as e:
            logger.error(f"Failed to open new stream on [{device_index}] {device_name}: {e}")
            logger.info("Keeping current audio device unchanged")
            return

        # Update state only after new stream is confirmed working
        AUDIO_DEVICE = device_name  # store name, not index
        AUDIO_DEVICE_HOSTAPI = device_hostapi
        AUDIO_DEVICE_INDEX = device_index
        AUDIO_DEVICE_UID = device_uid
        STATE.active_mic_name = device_name
        STATE.active_mic_index = device_index
        STATE.active_mic_hostapi = device_hostapi

        # Persist device identity to config.py
        save_audio_device_to_config(device_name, device_hostapi, device_index, device_uid=device_uid)

        try:
            STATE.last_device_topology_signature = _current_input_topology_signature()
        except Exception:
            pass

        # Update tray to ready state (handles recovery from error state)
        if STATE.model is not None:
            _set_ready_icon()

        # Refresh tray menu checkmarks
        if STATE.tray_icon:
            STATE.tray_icon.update_menu()

    except Exception as e:
        logger.error(f"Failed to switch audio device: {e}")
    finally:
        _switch_lock.release()


# Configuration - load from config.py if available
_CONFIG = None
try:
    import config as _CONFIG  # type: ignore
except Exception as e:
    logger.warning(f"Failed to load config.py ({type(e).__name__}: {e}). Using defaults.")


def _config_value(name, default):
    """Read a value from config.py with safe fallback."""
    if _CONFIG is None:
        return default
    return getattr(_CONFIG, name, default)


HOTKEY = _config_value('HOTKEY', 'alt+f')
MODEL_SIZE = _config_value('MODEL_SIZE', 'small')
DEVICE = _config_value('DEVICE', 'cuda')
COMPUTE_TYPE = _config_value('COMPUTE_TYPE', 'float16')
AUDIO_DEVICE = _config_value('AUDIO_DEVICE', None)
LANGUAGE = _config_value('LANGUAGE', 'en')
AUDIO_DEVICE_HOSTAPI = _config_value('AUDIO_DEVICE_HOSTAPI', None)
AUDIO_DEVICE_INDEX = _config_value('AUDIO_DEVICE_INDEX', None)
AUDIO_DEVICE_UID = _config_value('AUDIO_DEVICE_UID', None)
VOCABULARY = _config_value('VOCABULARY', '')
NOISE_REDUCTION = _config_value('NOISE_REDUCTION', False)
USE_CLIPBOARD = _config_value('USE_CLIPBOARD', False)
LOG_TRANSCRIPT_TEXT = _config_value('LOG_TRANSCRIPT_TEXT', False)
LOG_LEVEL = _config_value('LOG_LEVEL', None)
MAX_TYPED_CHARS = _config_value('MAX_TYPED_CHARS', 1000)
NOISE_GATE_THRESHOLD = _config_value('NOISE_GATE_THRESHOLD', 0.01)

if _CONFIG is not None:
    logger.info(f"Loaded config: HOTKEY={HOTKEY}, MODEL={MODEL_SIZE}, DEVICE={DEVICE}, LANGUAGE={LANGUAGE}")

if isinstance(AUDIO_DEVICE_INDEX, str):
    stripped = AUDIO_DEVICE_INDEX.strip()
    AUDIO_DEVICE_INDEX = int(stripped) if stripped.isdigit() else None

if isinstance(AUDIO_DEVICE_UID, str):
    AUDIO_DEVICE_UID = AUDIO_DEVICE_UID.strip() or None

if VOCABULARY:
    logger.info(f"Custom vocabulary: {VOCABULARY}")

if NOISE_REDUCTION and not NOISEREDUCE_AVAILABLE:
    logger.warning("NOISE_REDUCTION enabled but noisereduce not installed. Disabling.")
    NOISE_REDUCTION = False
elif NOISE_REDUCTION:
    logger.info("Noise reduction enabled")

if USE_CLIPBOARD:
    logger.info("Clipboard copy enabled")

if LOG_TRANSCRIPT_TEXT:
    logger.warning("Transcript text logging is enabled; this may capture sensitive data in logs")

if LOG_LEVEL:
    configured_level = getattr(logging, str(LOG_LEVEL).upper(), None)
    if isinstance(configured_level, int):
        logging.getLogger().setLevel(configured_level)
        _apply_noisy_logger_policy(configured_level)
        logger.info(f"Log level set from config: {str(LOG_LEVEL).upper()}")
    else:
        logger.warning(f"Ignoring invalid LOG_LEVEL value: {LOG_LEVEL!r}")

if not isinstance(MAX_TYPED_CHARS, int):
    MAX_TYPED_CHARS = 1000
if MAX_TYPED_CHARS < 1:
    MAX_TYPED_CHARS = 1

if NOISE_GATE_THRESHOLD > 0:
    logger.info(f"Noise gate enabled (threshold={NOISE_GATE_THRESHOLD})")

# Handle 'auto' language setting
TRANSCRIBE_LANGUAGE = None if LANGUAGE == 'auto' else LANGUAGE

SAMPLE_RATE = 16000

MAX_RECORDING_SECONDS = 120
RECORDING_MONITOR_INTERVAL = 0.05
IDLE_RECORDING_MONITOR_INTERVAL = 0.20
HOTKEY_PARTS = [part.strip() for part in HOTKEY.split('+') if part.strip()]
SILENCE_RMS_THRESHOLD = 1e-6
SILENCE_POWER_THRESHOLD = SILENCE_RMS_THRESHOLD * SILENCE_RMS_THRESHOLD

READY_TITLE = f'Voice Dictation - Ready [{HOTKEY.upper()}]'
RECORDING_TITLE = 'Voice Dictation - Recording...'
PROCESSING_TITLE = 'Voice Dictation - Processing...'
RECORDING_MUTED_WARNING_TITLE = 'Recording - Warning: mic may be muted'
READY_MUTED_WARNING_TITLE = f'{READY_TITLE} (Warning: mic may be muted)'

MODIFIER_KEYS = (
    'alt', 'left alt', 'right alt',
    'ctrl', 'left ctrl', 'right ctrl',
    'shift', 'left shift', 'right shift',
    'windows', 'left windows', 'right windows',
)

if not HOTKEY_PARTS:
    logger.warning(f"HOTKEY '{HOTKEY}' could not be parsed into keys")


def _set_ready_icon(title=None):
    """Set tray icon to ready (green)."""
    update_tray_icon('green', title or READY_TITLE)


def _set_recording_icon(title=None):
    """Set tray icon to recording (red)."""
    update_tray_icon('red', title or RECORDING_TITLE)


def _set_processing_icon(title=None):
    """Set tray icon to processing (yellow)."""
    update_tray_icon('yellow', title or PROCESSING_TITLE)


def _finish_processing_cycle():
    """Reset processing state and return tray icon to ready."""
    with STATE.lock:
        STATE.is_processing = False
    _set_ready_icon()


def _recording_snapshot():
    """Return a lock-consistent snapshot of recording state."""
    with STATE.lock:
        return STATE.is_recording, STATE.recording_start_time, STATE.silence_flag


def _is_audio_pipeline_busy():
    """Return True while recording or transcribing a captured utterance."""
    with STATE.lock:
        return STATE.is_recording or STATE.is_processing


def load_model():
    """Load Whisper model on GPU."""
    if STATE.model is None:
        logger.info(f"Loading {MODEL_SIZE} model on {DEVICE}...")
        from faster_whisper import WhisperModel
        STATE.model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        logger.info("Model loaded successfully")
    return STATE.model


def audio_callback(indata, frames, time_info, status):
    """Called for each audio block during recording."""
    STATE.last_callback_time = time.time()
    if status:
        logger.warning(f"Audio status: {status}")
    if not STATE.is_recording:
        return

    frame = indata.copy()
    # Lightweight silence detection: compare mean-square value to avoid sqrt in hot path.
    power = float(np.mean(frame * frame))
    is_silent = power < SILENCE_POWER_THRESHOLD

    with STATE.lock:
        if STATE.is_recording:
            STATE.recorded_frames.append(frame)
            STATE.silence_flag = is_silent


def start_recording():
    """Start recording audio."""
    with STATE.lock:
        if STATE.is_recording:
            return False
        if STATE.is_processing:
            logger.info("Ignoring hotkey press while prior transcription is still processing")
            return False
        STATE.recorded_frames = []
        STATE.silence_flag = False
        STATE.recording_start_time = time.time()
        STATE.is_recording = True
    _set_recording_icon()
    logger.info("Recording started")
    return True


def _begin_processing_from_recording():
    """Transition recording -> processing and return captured frames."""
    with STATE.lock:
        if not STATE.is_recording:
            return None
        STATE.is_recording = False
        if STATE.is_processing:
            logger.debug("stop_recording ignored: transcription already in progress")
            return None
        STATE.is_processing = True
        recorded_frames = STATE.recorded_frames
        STATE.recorded_frames = []
    return recorded_frames


def _prepare_audio_for_transcription(recorded_frames):
    """Validate and normalize captured frames for transcription."""
    if not recorded_frames:
        logger.info("No audio captured")
        return None

    # Combine recorded audio with corruption guard
    try:
        audio_data = np.concatenate(recorded_frames, axis=0)
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to concatenate audio frames (corrupt data?): {e}")
        return None

    # Check minimum duration (< 0.1s is too short to transcribe)
    duration_s = len(audio_data) / SAMPLE_RATE
    if duration_s < 0.1:
        logger.info(f"Audio too short ({duration_s:.3f}s < 0.1s), skipping transcription")
        return None

    # Check noise gate threshold
    if NOISE_GATE_THRESHOLD > 0:
        power = float(np.mean(audio_data * audio_data))
        if power < (NOISE_GATE_THRESHOLD * NOISE_GATE_THRESHOLD):
            rms = power ** 0.5
            logger.info(f"Audio too quiet (RMS={rms:.4f} < {NOISE_GATE_THRESHOLD}), skipping")
            return None

    # Apply noise reduction if enabled
    if NOISE_REDUCTION:
        logger.debug("Applying noise reduction...")
        # Use ravel() to avoid unnecessary copy when data is already contiguous.
        audio_data = nr.reduce_noise(y=np.ravel(audio_data), sr=SAMPLE_RATE)
    return audio_data


def _transcribe_and_emit_text(audio_data):
    """Run Whisper transcription then emit text to clipboard/active window."""
    start_time = time.time()
    transcribe_opts = {
        'beam_size': 5,
        'language': TRANSCRIBE_LANGUAGE,
    }
    if VOCABULARY:
        transcribe_opts['initial_prompt'] = VOCABULARY
    raw_text = transcription_io.transcribe_audio_array(
        STATE.model,
        audio_data,
        **transcribe_opts,
    )
    text = transcription_io.sanitize_transcript_text(raw_text)
    if len(text) > MAX_TYPED_CHARS:
        logger.warning(
            f"Transcript length {len(text)} exceeds MAX_TYPED_CHARS={MAX_TYPED_CHARS}; truncating output"
        )
        text = text[:MAX_TYPED_CHARS].rstrip()
    elapsed = time.time() - start_time

    if not text:
        logger.info("No speech detected")
        return

    if raw_text != text:
        logger.info("Transcript normalized before output")
    if LOG_TRANSCRIPT_TEXT:
        logger.info(f"Transcribed ({elapsed:.1f}s): {text[:50]}...")
    else:
        logger.info(f"Transcribed ({elapsed:.1f}s), {len(text)} chars")

    # Copy to clipboard if enabled
    if USE_CLIPBOARD:
        pyperclip.copy(text)

    # Type the text into active window
    # Small delay to ensure window focus
    time.sleep(0.05)
    _release_modifier_keys()
    # Add delay between keystrokes to prevent Claude Code crash
    # (Known bug: rapid text injection causes TUI crash)
    keyboard.write(text, delay=0.01, restore_state_after=True)  # 10ms between characters


def stop_recording_and_transcribe():
    """Stop recording, transcribe captured audio, and inject resulting text."""
    recorded_frames = _begin_processing_from_recording()
    if recorded_frames is None:
        return

    try:
        _set_processing_icon()
        logger.info("Processing audio...")
        audio_data = _prepare_audio_for_transcription(recorded_frames)
        if audio_data is None:
            return
        _transcribe_and_emit_text(audio_data)
    except Exception as e:
        logger.error(f"Transcription error: {e}")
    finally:
        _finish_processing_cycle()


def on_hotkey_press():
    """Called when hotkey is pressed."""
    start_recording()


def on_hotkey_release():
    """Called when hotkey is released."""
    is_recording, _, _ = _recording_snapshot()
    if is_recording:
        stop_recording_and_transcribe()


def _is_hotkey_currently_pressed():
    """Best-effort hotkey state check for release-callback fallback logic."""
    if not HOTKEY_PARTS:
        return True
    try:
        return all(keyboard.is_pressed(key) for key in HOTKEY_PARTS)
    except Exception as e:
        logger.debug(f"Hotkey state check failed: {e}")
        return True


def _release_modifier_keys():
    """Best-effort release of modifier keys before synthetic typing."""
    for key in MODIFIER_KEYS:
        try:
            keyboard.release(key)
        except Exception:
            pass


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

    def make_mic_callback(dev_idx, dev_name, dev_hostapi, dev_uid):
        def callback(icon, item):
            switch_audio_device(dev_idx, dev_name, dev_hostapi, dev_uid)
        return callback

    def make_mic_checked(dev_idx):
        def is_checked(item):
            return STATE.active_mic_index == dev_idx
        return is_checked

    items = []
    for dev_idx, dev_name, dev_hostapi, _, dev_uid in input_devices:
        label = f"{dev_name} [{dev_hostapi}]"
        display_name = label if len(label) <= 64 else label[:61] + '...'
        items.append(
            pystray.MenuItem(
                display_name,
                make_mic_callback(dev_idx, dev_name, dev_hostapi, dev_uid),
                checked=make_mic_checked(dev_idx),
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
        pystray.MenuItem('Run Startup Healthcheck...', on_tray_healthcheck),
        pystray.MenuItem('Calibrate Noise Gate...', on_tray_calibrate),
        pystray.MenuItem('Exit', on_tray_exit)
    )


def run_dictation_loop():
    """Run the main dictation loop (hotkey callbacks + recording watchdog)."""
    logger.info(f"Registering hotkey: {HOTKEY}")
    keyboard.add_hotkey(HOTKEY, on_hotkey_press, suppress=True, trigger_on_release=False)
    keyboard.add_hotkey(HOTKEY, on_hotkey_release, suppress=True, trigger_on_release=True)
    logger.info("Hotkey registered. Ready for dictation!")

    watchdog_thread = threading.Thread(target=_recording_state_watchdog, daemon=True)
    watchdog_thread.start()
    logger.info("Recording watchdog thread started")

    # Keep running until interrupted
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, exiting...")


def _recording_state_watchdog():
    """Monitor timeout/silence and fallback key-release detection while recording."""
    silence_warned = False
    while not STATE.shutdown_event.is_set():
        is_recording, recording_start_time, silence_flag = _recording_snapshot()

        if is_recording and recording_start_time > 0:
            if not _is_hotkey_currently_pressed():
                logger.info("Recording stop fallback triggered: hotkey no longer pressed")
                silence_warned = False
                stop_recording_and_transcribe()
                STATE.shutdown_event.wait(RECORDING_MONITOR_INTERVAL)
                continue

            elapsed = time.time() - recording_start_time
            if elapsed > MAX_RECORDING_SECONDS:
                logger.warning(f"Recording timeout after {MAX_RECORDING_SECONDS}s - force stopping")
                silence_warned = False
                stop_recording_and_transcribe()
                STATE.shutdown_event.wait(RECORDING_MONITOR_INTERVAL)
                continue

            if silence_flag and not silence_warned:
                silence_warned = True
                _set_recording_icon(RECORDING_MUTED_WARNING_TITLE)
            elif not silence_flag and silence_warned:
                silence_warned = False
                _set_recording_icon()
        else:
            silence_warned = False

        wait_seconds = RECORDING_MONITOR_INTERVAL if is_recording else IDLE_RECORDING_MONITOR_INTERVAL
        STATE.shutdown_event.wait(wait_seconds)


def test_microphone():
    """Non-blocking microphone test: record 0.5s and check if audio level is reasonable.
    Warns via log and tray tooltip if the mic appears muted, but never blocks startup.
    Must be called after the main stream is started.
    """
    manager = _get_stream_manager()
    if not manager.is_active:
        logger.warning("test_microphone: audio stream not active, skipping test")
        return

    logger.info("Running microphone self-test (0.5s capture)...")

    try:
        test_device = _get_active_stream_device()
        test_audio = audio_capture.capture_from_stream(
            sd,
            device_index=test_device,
            seconds=0.5,
            sample_rate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            blocksize=1024,
            logger=logger,
        )
    except Exception as e:
        logger.warning(f"Microphone self-test failed to capture audio: {e}")
        return

    if test_audio.size == 0:
        logger.warning("Microphone self-test: no frames captured")
        return

    try:
        rms = np.sqrt(np.mean(test_audio ** 2))
    except Exception as e:
        logger.warning(f"Microphone self-test: error computing RMS: {e}")
        return

    if rms < 1e-6:
        logger.warning(f"Microphone self-test: RMS={rms:.8f} - mic may be muted or disconnected")
        _set_ready_icon(READY_MUTED_WARNING_TITLE)
    else:
        logger.info(f"Microphone self-test passed (RMS={rms:.6f})")


def _reopen_audio_stream(recovery_reason):
    """Try to reopen the current stream target. Returns True on success."""
    if not _switch_lock.acquire(blocking=False):
        logger.info(f"Skipping stream recovery ({recovery_reason}): switch already in progress")
        return False

    update_tray_icon('gray', f'Voice Dictation - Recovering audio ({recovery_reason})')

    try:
        device_to_open = _get_active_stream_device()
        manager = _get_stream_manager()
        manager.reopen(device_to_open)
        STATE.audio_stream = manager.stream
        STATE.last_callback_time = time.time()
        logger.info(
            f"Audio stream recovered successfully ({recovery_reason}) on open_arg={device_to_open}"
        )
        if STATE.model is not None:
            _set_ready_icon()
        _write_runtime_state('ready', reason=f'recovered:{recovery_reason}')
        return True
    except Exception as e:
        logger.error(f"Failed to recover audio stream ({recovery_reason}): {e}")
        update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')
        _write_runtime_state('audio_error', reason=f'recovery_failed:{recovery_reason}', details=str(e))
        return False
    finally:
        _switch_lock.release()


def stream_health_watchdog():
    """Daemon thread that monitors audio stream health.
    Checks every 5 seconds whether:
    1) Input device topology changed (dock/undock, plug/unplug)
    2) The stream is active and receiving callbacks
    If unhealthy, attempts automatic recovery.
    """
    logger.info("Stream health watchdog started")

    while not STATE.shutdown_event.is_set():
        STATE.shutdown_event.wait(5)
        if STATE.shutdown_event.is_set():
            break

        # Don't interfere while actively recording.
        if _is_audio_pipeline_busy():
            continue

        # Detect device topology changes (dock/undock, device add/remove).
        try:
            input_devices = _enumerate_input_devices()
        except Exception as e:
            logger.warning(f"Failed to enumerate devices in watchdog: {e}")
            input_devices = []

        if input_devices:
            current_signature = _current_input_topology_signature(input_devices)
            if STATE.last_device_topology_signature is None:
                STATE.last_device_topology_signature = current_signature
            elif current_signature != STATE.last_device_topology_signature:
                logger.info("Detected input device topology change. Re-resolving preferred microphone.")
                STATE.last_device_topology_signature = current_signature
                previous_index = STATE.active_mic_index
                if check_microphone():
                    if STATE.tray_icon:
                        STATE.tray_icon.update_menu()
                    if STATE.audio_stream is None or STATE.active_mic_index != previous_index:
                        _reopen_audio_stream('device topology change')
                else:
                    logger.warning("No usable microphone after topology change")
                    update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
                    _write_runtime_state('audio_error', reason='no_microphone_after_topology_change')
                    _get_stream_manager().close()
                    STATE.audio_stream = None
                continue
        else:
            if STATE.last_device_topology_signature not in (None, ()):
                logger.warning("No input devices currently available")
            STATE.last_device_topology_signature = ()
            update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
            _write_runtime_state('audio_error', reason='no_input_devices')
            _get_stream_manager().close()
            STATE.audio_stream = None
            continue

        # Recover missing stream when devices exist.
        manager = _get_stream_manager()
        if manager.stream is None:
            if STATE.active_mic_index is None and not check_microphone():
                update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
                continue
            _reopen_audio_stream('stream missing')
            continue

        stream_active = manager.is_active

        callback_stale = False
        if STATE.last_callback_time > 0:
            callback_stale = (time.time() - STATE.last_callback_time) > 10

        if not stream_active or callback_stale:
            reason = "stream inactive" if not stream_active else "no callbacks for >10s"
            logger.error(f"Audio stream appears dead ({reason}). Attempting recovery...")
            _reopen_audio_stream(reason)


def init_audio_and_dictation():
    """Background initialization: mic check, model load, audio stream, hotkey registration.
    The tray icon is already visible (gray) when this runs.
    """
    mic_ok = False

    # Check microphone with fallback chain
    logger.info("Checking for microphone...")
    mic_ok = check_microphone()
    if not mic_ok:
        update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
        logger.error("No usable microphone found. Connect a device and select it from the tray menu.")
        _write_runtime_state('audio_error', reason='no_usable_microphone')

    # Load model regardless of mic status (so it's ready when user plugs in a mic)
    try:
        update_tray_icon('gray', 'Voice Dictation - Loading model...')
        logger.info("Loading Whisper model...")
        load_model()
    except Exception as e:
        logger.exception(f"Failed to load Whisper model: {e}")
        update_tray_icon('gray', 'Voice Dictation - Model load failed (see log)')
        _write_runtime_state('audio_error', reason='model_load_failed', details=str(e))
        # Still run the hotkey loop so the tray stays alive
        run_dictation_loop()
        return

    # Start audio stream if mic was found
    if mic_ok:
        try:
            device_to_open = _get_active_stream_device()
            logger.info(
                f"Opening audio stream on device index={STATE.active_mic_index} "
                f"name={AUDIO_DEVICE} open_arg={device_to_open}..."
            )
            manager = _get_stream_manager()
            manager.open(device_to_open)
            STATE.audio_stream = manager.stream
            STATE.last_callback_time = time.time()
            logger.info("Audio stream started")
            _set_ready_icon()
            _write_runtime_state('ready', reason='startup_complete')

            # Run non-blocking mic test after stream is confirmed working
            test_microphone()

            # Start stream health watchdog daemon
            watchdog_thread = threading.Thread(target=stream_health_watchdog, daemon=True)
            watchdog_thread.start()
        except Exception as e:
            logger.exception(f"Failed to open audio stream: {e}")
            update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')
            _get_stream_manager().close()
            STATE.audio_stream = None
            _write_runtime_state('audio_error', reason='stream_open_failed', details=str(e))
    else:
        update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
        _write_runtime_state('audio_error', reason='no_usable_microphone')

    # Register hotkey and block (dictation works if stream is active)
    run_dictation_loop()


def main():
    try:
        # Ensure only one instance runs
        STATE.shutdown_event.clear()
        check_single_instance()
        _write_runtime_state('starting', reason='process_boot')
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
            STATE.tray_icon = pystray.Icon(
                'voice-dictation',
                create_tray_image('gray'),
                'Voice Dictation - Starting...',
                menu
            )
            STATE.tray_color = 'gray'
            STATE.tray_title = 'Voice Dictation - Starting...'

            # Run mic check, model load, and dictation in background thread
            init_thread = threading.Thread(target=init_audio_and_dictation, daemon=True)
            init_thread.start()

            # Run tray icon on main thread (blocks until exit)
            logger.info("Starting system tray icon")
            STATE.tray_icon.run()
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
