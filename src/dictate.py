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
import uuid
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
from voice_dictation import recording_pipeline, watchdog_loops

# Set up logging FIRST before any other imports that might fail
LOG_DIR = os.path.join(os.path.expanduser('~'), 'voice-dictation')
LOG_FILE = os.path.join(LOG_DIR, 'dictation.log')
os.makedirs(LOG_DIR, exist_ok=True)

_DEFAULT_LOG_LEVEL_NAME = os.environ.get('VOICE_DICTATION_LOG_LEVEL', 'INFO').upper()
_DEFAULT_LOG_LEVEL = getattr(logging, _DEFAULT_LOG_LEVEL_NAME, logging.INFO)
if not isinstance(_DEFAULT_LOG_LEVEL, int):
    _DEFAULT_LOG_LEVEL = logging.INFO

try:
    RESTART_MUTEX_WAIT_SECONDS = max(0, int(os.environ.get('VOICE_DICTATION_RESTART_MUTEX_WAIT_SECONDS', '30')))
except (TypeError, ValueError):
    RESTART_MUTEX_WAIT_SECONDS = 30

SESSION_ID = uuid.uuid4().hex[:12]
RESTART_AFTER_PID = None


class SessionFilter(logging.Filter):
    """Inject session_id into every log record."""

    def filter(self, record):
        record.session_id = SESSION_ID
        return True


_BASE_LOG_RECORD_FACTORY = logging.getLogRecordFactory()


def _session_record_factory(*args, **kwargs):
    """Ensure all log records have a session_id used by the formatter."""
    record = _BASE_LOG_RECORD_FACTORY(*args, **kwargs)
    if not hasattr(record, 'session_id'):
        record.session_id = SESSION_ID
    return record


logging.setLogRecordFactory(_session_record_factory)


logging.basicConfig(
    level=_DEFAULT_LOG_LEVEL,
    format='%(asctime)s [%(levelname)s] [%(process)d/%(session_id)s] %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.getLogger().addFilter(SessionFilter())
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
    logger.debug("keyboard imported OK")
except Exception as e:
    logger.error(f"Failed to import keyboard: {e}")
    raise

try:
    import sounddevice as sd
    logger.debug("sounddevice imported OK")
except Exception as e:
    logger.error(f"Failed to import sounddevice: {e}")
    raise

try:
    import numpy as np
    logger.debug("numpy imported OK")
except Exception as e:
    logger.error(f"Failed to import numpy: {e}")
    raise

try:
    import pyperclip
    logger.debug("pyperclip imported OK")
except Exception as e:
    logger.error(f"Failed to import pyperclip: {e}")
    raise

try:
    import pystray
    from PIL import Image, ImageDraw
    logger.debug("pystray imported OK")
    TRAY_AVAILABLE = True
except Exception as e:
    logger.warning(f"pystray not available, will use console mode: {e}")
    TRAY_AVAILABLE = False

try:
    import noisereduce as nr
    logger.debug("noisereduce imported OK")
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
            if prior_name is not None:
                STATE.device_fallback_count += 1
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


def check_single_instance(retry_seconds=0, poll_seconds=0.5):
    """Ensure only one instance runs. Exit silently if already running.

    Primary guard: named OS mutex (survives stale lock files and process-ID reuse).
    Secondary guard: PID lock file for diagnostics and manual recovery visibility.
    """
    logger.info(f"Checking single instance. Mutex: {MUTEX_NAME}")
    deadline = time.time() + max(0.0, float(retry_seconds))
    while True:
        if _acquire_single_instance_mutex():
            break
        remaining = deadline - time.time()
        if remaining <= 0:
            logger.info("Another Voice Dictation instance already owns the mutex. Exiting.")
            sys.exit(0)
        wait_s = min(max(0.1, float(poll_seconds)), remaining)
        logger.info(f"Another instance owns the mutex. Waiting {wait_s:.1f}s for restart handoff...")
        time.sleep(wait_s)

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


def cleanup_resources(shutdown_status='shutdown_clean', shutdown_reason='cleanup_resources', extra_details=None):
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

    # Persist final lifecycle marker with session metrics before hard exit.
    details = {
        'session_id': STATE.session_id,
        'utterance_count': STATE.utterance_count,
        'total_recording_ms': STATE.total_recording_ms,
        'total_chars_typed': STATE.total_chars_typed,
        'device_fallback_count': STATE.device_fallback_count,
        'transcription_errors': STATE.transcription_errors,
    }
    if isinstance(extra_details, dict):
        details.update(extra_details)
    _write_runtime_state(shutdown_status, reason=shutdown_reason, details=details)

    # Give daemon threads up to 2s to finish before hard exit
    logger.info("Waiting up to 2s for threads to finish...")
    time.sleep(2)

    # os._exit as last step - required because pystray/keyboard threads
    # can prevent a clean sys.exit()
    os._exit(0)


def on_tray_restart(icon, item):
    """Restart the application by spawning a new process then exiting."""
    import subprocess

    logger.info("Restart requested from tray menu")
    my_pid = os.getpid()
    script_path = os.path.abspath(__file__)
    python_exe = sys.executable
    # Prefer pythonw for background operation (no console window)
    pythonw_exe = python_exe.replace('python.exe', 'pythonw.exe')
    if not os.path.isfile(pythonw_exe):
        pythonw_exe = python_exe

    try:
        subprocess.Popen(
            [pythonw_exe, script_path, '--restart-after-pid', str(my_pid)],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        logger.info(f"Spawned restart process (waiting on PID {my_pid})")
    except Exception as e:
        logger.error(f"Failed to spawn restart process: {e}")
        return

    icon.stop()
    cleanup_resources(
        shutdown_status='restarting',
        shutdown_reason='tray_restart_requested',
        extra_details={'restart_parent_pid': my_pid}
    )


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


def on_tray_diagnostics(icon, item):
    """Launch diagnostic log analyzer in a separate console window."""
    logger.info("Launching diagnostics tool...")
    _launch_script_in_console('diagnostics.py')


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
                logger.debug("Failed to build device UID", exc_info=True)

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
                logger.debug("Failed to query device hostapi", exc_info=True)
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
            logger.debug("Failed to compute topology signature", exc_info=True)

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


def _coerce_float_config(value, default):
    """Parse float config values that may be provided as strings."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return float(stripped)
            except ValueError:
                pass
    return float(default)


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
NOISE_GATE_THRESHOLD = _coerce_float_config(_config_value('NOISE_GATE_THRESHOLD', 0.01), 0.01)
NOISE_GATE_PEAK_MULTIPLIER = _coerce_float_config(_config_value('NOISE_GATE_PEAK_MULTIPLIER', 3.0), 3.0)

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
    try:
        MAX_TYPED_CHARS = int(str(MAX_TYPED_CHARS).strip())
    except (TypeError, ValueError):
        MAX_TYPED_CHARS = 1000
if MAX_TYPED_CHARS < 1:
    MAX_TYPED_CHARS = 1

if NOISE_GATE_PEAK_MULTIPLIER < 1.0:
    NOISE_GATE_PEAK_MULTIPLIER = 1.0

if NOISE_GATE_THRESHOLD > 0:
    logger.info(
        f"Noise gate enabled (threshold={NOISE_GATE_THRESHOLD}, "
        f"peak_multiplier={NOISE_GATE_PEAK_MULTIPLIER})"
    )

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
RECORDING_RELEASE_FALLBACK_LOG = 'Recording stop fallback triggered: hotkey no longer pressed'

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
    return recording_pipeline.begin_processing_from_recording(STATE, logger)


def _prepare_audio_for_transcription(recorded_frames):
    """Validate and normalize captured frames for transcription."""
    return recording_pipeline.prepare_audio_for_transcription(
        recorded_frames,
        np_module=np,
        sample_rate=SAMPLE_RATE,
        noise_gate_threshold=NOISE_GATE_THRESHOLD,
        noise_gate_peak_multiplier=NOISE_GATE_PEAK_MULTIPLIER,
        noise_reduction=NOISE_REDUCTION,
        noise_reducer_module=nr if NOISEREDUCE_AVAILABLE else None,
        logger=logger,
    )


def _transcribe_and_emit_text(audio_data):
    """Run Whisper transcription then emit text to clipboard/active window."""
    result = recording_pipeline.transcribe_audio(
        audio_data,
        model=STATE.model,
        transcription_io_module=transcription_io,
        sample_rate=SAMPLE_RATE,
        transcribe_language=TRANSCRIBE_LANGUAGE,
        vocabulary=VOCABULARY,
        max_typed_chars=MAX_TYPED_CHARS,
        logger=logger,
    )
    raw_text = result['raw_text']
    text = result['text']
    transcription_ms = result['transcription_ms']

    if not text:
        logger.info("No speech detected")
        return

    STATE.utterance_count += 1
    STATE.total_recording_ms += int(transcription_ms)

    if raw_text != text:
        logger.info("Transcript normalized before output")
    if LOG_TRANSCRIPT_TEXT:
        logger.info(f"Transcribed ({transcription_ms:.0f}ms): {text[:50]}...")
    else:
        logger.info(f"Transcribed ({transcription_ms:.0f}ms), {len(text)} chars")

    # Copy to clipboard if enabled
    if USE_CLIPBOARD:
        pyperclip.copy(text)

    # Type the text into active window
    # Small delay to ensure window focus
    time.sleep(0.05)
    _release_modifier_keys()
    # Add delay between keystrokes to prevent Claude Code crash
    # (Known bug: rapid text injection causes TUI crash)
    keyboard.write(text, delay=0.01, restore_state_after=False)  # 10ms between characters
    STATE.total_chars_typed += len(text)


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
        STATE.transcription_errors += 1
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
            logger.debug("Failed to release modifier key %s", key, exc_info=True)


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
        pystray.MenuItem('View Diagnostics...', on_tray_diagnostics),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Restart', on_tray_restart),
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
    def _hotkey_pressed_for_watchdog():
        if not _is_hotkey_currently_pressed():
            return False
        return True

    watchdog_loops.run_recording_state_watchdog(
        state=STATE,
        shutdown_event=STATE.shutdown_event,
        get_recording_snapshot=_recording_snapshot,
        is_hotkey_currently_pressed=_hotkey_pressed_for_watchdog,
        stop_recording_and_transcribe=stop_recording_and_transcribe,
        set_recording_icon=_set_recording_icon,
        recording_muted_warning_title=RECORDING_MUTED_WARNING_TITLE,
        recording_monitor_interval=RECORDING_MONITOR_INTERVAL,
        idle_recording_monitor_interval=IDLE_RECORDING_MONITOR_INTERVAL,
        max_recording_seconds=MAX_RECORDING_SECONDS,
        release_fallback_message=RECORDING_RELEASE_FALLBACK_LOG,
        logger=logger,
    )


def test_microphone():
    """Non-blocking microphone test: record 0.5s and check if audio level is reasonable.
    Warns via log and tray tooltip if the mic appears muted, but never blocks startup.
    Must be called after the main stream is started.
    """
    manager = _get_stream_manager()
    watchdog_loops.run_microphone_self_test(
        manager=manager,
        get_active_stream_device=_get_active_stream_device,
        capture_from_stream_fn=audio_capture.capture_from_stream,
        sd_module=sd,
        sample_rate=SAMPLE_RATE,
        np_module=np,
        logger=logger,
        set_ready_icon=_set_ready_icon,
        ready_muted_warning_title=READY_MUTED_WARNING_TITLE,
    )


def _reopen_audio_stream(recovery_reason):
    """Try to reopen the current stream target. Returns True on success."""
    return watchdog_loops.reopen_audio_stream(
        recovery_reason=recovery_reason,
        switch_lock=_switch_lock,
        update_tray_icon=update_tray_icon,
        get_active_stream_device=_get_active_stream_device,
        get_stream_manager=_get_stream_manager,
        state=STATE,
        logger=logger,
        set_ready_icon=_set_ready_icon,
        write_runtime_state=_write_runtime_state,
    )


def stream_health_watchdog():
    """Daemon thread that monitors audio stream health.
    Checks every 5 seconds whether:
    1) Input device topology changed (dock/undock, plug/unplug)
    2) The stream is active and receiving callbacks
    If unhealthy, attempts automatic recovery with exponential backoff.
    """
    watchdog_loops.run_stream_health_watchdog(
        state=STATE,
        shutdown_event=STATE.shutdown_event,
        is_audio_pipeline_busy=_is_audio_pipeline_busy,
        enumerate_input_devices=_enumerate_input_devices,
        current_input_topology_signature=_current_input_topology_signature,
        check_microphone=check_microphone,
        reopen_audio_stream_fn=_reopen_audio_stream,
        update_tray_icon=update_tray_icon,
        write_runtime_state=_write_runtime_state,
        get_stream_manager=_get_stream_manager,
        logger=logger,
    )


def _heartbeat_loop():
    """Update state.json every 60s so external tools can detect a hung process."""
    while not STATE.shutdown_event.wait(timeout=60):
        _write_runtime_state('heartbeat')


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
        STATE.session_id = SESSION_ID
        retry_seconds = RESTART_MUTEX_WAIT_SECONDS if RESTART_AFTER_PID is not None else 0
        check_single_instance(retry_seconds=retry_seconds)
        _write_runtime_state('starting', reason='process_boot')
        logger.info("Starting main()")

        # Start heartbeat daemon so external tools can detect hung processes
        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        heartbeat_thread.start()

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


def _wait_for_pid_exit(pid, timeout_s=15):
    """Block until *pid* is no longer running, or until *timeout_s* elapses."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        # Process already gone or inaccessible
        logger.info(f"PID {pid} already exited (OpenProcess returned 0)")
        return

    try:
        timeout_ms = int(timeout_s * 1000)
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result == WAIT_OBJECT_0:
            logger.info(f"PID {pid} exited cleanly")
        else:
            logger.warning(f"Timed out waiting for PID {pid} to exit (WaitForSingleObject={result})")
    finally:
        kernel32.CloseHandle(handle)


if __name__ == '__main__':
    # Handle restart: wait for the previous process to release the mutex
    if '--restart-after-pid' in sys.argv:
        idx = sys.argv.index('--restart-after-pid')
        if idx + 1 < len(sys.argv):
            try:
                old_pid = int(sys.argv[idx + 1])
                RESTART_AFTER_PID = old_pid
                logger.info(f"Restart mode: waiting for PID {old_pid} to exit")
                _wait_for_pid_exit(old_pid)
            except ValueError:
                logger.warning(f"Invalid PID value: {sys.argv[idx + 1]}")

    logger.info("Script entry point")
    main()
