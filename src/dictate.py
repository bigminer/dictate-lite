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
import hashlib
import json
from datetime import datetime

# Set up logging FIRST before any other imports that might fail
LOG_DIR = os.path.join(os.path.expanduser('~'), 'voice-dictation')
LOG_FILE = os.path.join(LOG_DIR, 'dictation.log')
os.makedirs(LOG_DIR, exist_ok=True)

STATE_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'VoiceDictation')
STATE_FILE = os.path.join(STATE_DIR, 'state.json')
os.makedirs(STATE_DIR, exist_ok=True)

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
logger.info(f"State file: {STATE_FILE}")

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
MUTEX_NAME = r'Local\VoiceDictationSingleton'
_instance_mutex_handle = None

# Active microphone name (set by check_microphone)
active_mic_name = None

# Active microphone index (used for opening streams to avoid name ambiguity)
active_mic_index = None

# Active microphone host API name (MME, WASAPI, DirectSound, etc.)
active_mic_hostapi = None

# Active audio stream (managed manually for hot-swap device switching)
audio_stream = None

# Last observed input-device topology signature (for hotplug detection)
last_device_topology_signature = None

# Lock to prevent concurrent device switches
_switch_lock = threading.Lock()

def _normalize_device_name(name):
    """Normalize device name for stable identity hashing."""
    return ' '.join(str(name).strip().lower().split())


def _build_device_uid(device_name, hostapi_name, device_info):
    """Build a stable UID from microphone metadata."""
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
    """Return input tuples: (index, name, hostapi_name, hostapi_index, device_uid)."""
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
        result.append((idx, dev['name'], hostapi_name, hostapi_index, device_uid))
    return result


def _choose_candidate(candidates, preferred_index=None, default_index=None):
    """Choose a device tuple from candidates with deterministic preference order."""
    if not candidates:
        return None

    if preferred_index is not None:
        for candidate in candidates:
            if candidate[0] == preferred_index:
                return candidate

    if default_index is not None:
        for candidate in candidates:
            if candidate[0] == default_index:
                return candidate

    wasapi = [c for c in candidates if c[2] == 'Windows WASAPI']
    if wasapi:
        return wasapi[0]

    return candidates[0]


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
    if not device_name:
        return None, None, None, None

    exact = [d for d in input_devices if d[1] == device_name]
    if preferred_hostapi:
        exact_host = [d for d in exact if d[2] == preferred_hostapi]
        if exact_host:
            exact = exact_host

    chosen = _choose_candidate(exact, preferred_index=preferred_index, default_index=default_index)
    if chosen:
        if len(exact) > 1:
            logger.warning(
                f"Multiple exact matches for '{device_name}'. Using [{chosen[0]}] '{chosen[1]}' ({chosen[2]})."
            )
        return chosen[0], chosen[1], chosen[2], chosen[4]

    partial = [d for d in input_devices if device_name in d[1] or d[1] in device_name]
    if preferred_hostapi:
        partial_host = [d for d in partial if d[2] == preferred_hostapi]
        if partial_host:
            partial = partial_host

    chosen = _choose_candidate(partial, preferred_index=preferred_index, default_index=default_index)
    if chosen:
        logger.info(
            f"Matched device by substring: '{device_name}' -> [{chosen[0]}] '{chosen[1]}' ({chosen[2]})"
        )
        return chosen[0], chosen[1], chosen[2], chosen[4]

    return None, None, None, None


def _resolve_device_uid_to_index(device_uid, input_devices, default_index=None):
    """Resolve saved UID to an input device tuple."""
    if not isinstance(device_uid, str) or not device_uid.strip():
        return None, None, None, None

    matches = [d for d in input_devices if d[4] == device_uid]
    chosen = _choose_candidate(matches, default_index=default_index)
    if chosen:
        return chosen[0], chosen[1], chosen[2], chosen[4]

    return None, None, None, None


def check_microphone():
    """Check microphone with fallback: saved device -> system default -> first available.
    Returns True if a usable mic was found, False otherwise.
    Updates AUDIO_DEVICE identity globals and active microphone globals.

    AUDIO_DEVICE can be:
      - None: use system default
      - str:  device name to resolve (current format)
      - int:  legacy device index (deprecated, will be migrated to name)
    """
    global active_mic_name, active_mic_index, active_mic_hostapi
    global AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID
    global last_device_topology_signature
    global last_device_topology_signature
    active_mic_name = None
    active_mic_index = None
    active_mic_hostapi = None
    try:
        input_devices = _enumerate_input_devices()
        if not input_devices:
            logger.error("No input devices found on this system")
            return False

        logger.info(f"Found {len(input_devices)} input device(s)")
        for idx, name, hostapi_name, _, device_uid in input_devices:
            logger.debug(f"  [{idx}] {name} ({hostapi_name})")
            logger.debug(f"    uid={device_uid}")
        last_device_topology_signature = _current_input_topology_signature(input_devices)

        device_by_index = {
            idx: (name, hostapi_name, device_uid)
            for idx, name, hostapi_name, _, device_uid in input_devices
        }

        default_idx = sd.default.device[0]
        if not isinstance(default_idx, int) or default_idx < 0:
            default_idx = None

        # Try saved UID first (strongest identity)
        if AUDIO_DEVICE_UID:
            resolved_idx, resolved_name, resolved_hostapi, resolved_uid = _resolve_device_uid_to_index(
                AUDIO_DEVICE_UID, input_devices, default_index=default_idx
            )
            if resolved_idx is not None:
                prior_name = AUDIO_DEVICE
                prior_hostapi = AUDIO_DEVICE_HOSTAPI
                prior_index = AUDIO_DEVICE_INDEX
                prior_uid = AUDIO_DEVICE_UID
                active_mic_name = resolved_name
                active_mic_index = resolved_idx
                active_mic_hostapi = resolved_hostapi
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
                    f"Using saved device UID: [{resolved_idx}] {active_mic_name} ({resolved_hostapi}) uid={resolved_uid}"
                )
                return True
            logger.warning(f"Saved device UID '{AUDIO_DEVICE_UID}' not found, falling back to name/default")

        # Try saved device name from config next
        if AUDIO_DEVICE is not None:
            if isinstance(AUDIO_DEVICE, str):
                resolved_idx, resolved_name, resolved_hostapi, resolved_uid = _resolve_device_name_to_index(
                    AUDIO_DEVICE,
                    input_devices,
                    preferred_hostapi=AUDIO_DEVICE_HOSTAPI,
                    preferred_index=AUDIO_DEVICE_INDEX,
                    default_index=default_idx
                )
                if resolved_idx is not None:
                    prior_hostapi = AUDIO_DEVICE_HOSTAPI
                    prior_index = AUDIO_DEVICE_INDEX
                    prior_uid = AUDIO_DEVICE_UID
                    active_mic_name = resolved_name
                    active_mic_index = resolved_idx
                    active_mic_hostapi = resolved_hostapi
                    AUDIO_DEVICE = resolved_name
                    AUDIO_DEVICE_HOSTAPI = resolved_hostapi
                    AUDIO_DEVICE_INDEX = resolved_idx
                    AUDIO_DEVICE_UID = resolved_uid
                    if (
                        prior_hostapi != resolved_hostapi
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
                        f"Using saved device identity: [{resolved_idx}] {active_mic_name} ({resolved_hostapi}) uid={resolved_uid}"
                    )
                    return True
                else:
                    logger.warning(f"Saved device name '{AUDIO_DEVICE}' not found, falling back to default")
            elif isinstance(AUDIO_DEVICE, int):
                logger.warning(f"AUDIO_DEVICE is an integer ({AUDIO_DEVICE}). Integer indices are deprecated.")
                try:
                    legacy_index = AUDIO_DEVICE
                    legacy_entry = device_by_index.get(legacy_index)
                    if legacy_entry is not None:
                        legacy_name, legacy_hostapi, legacy_uid = legacy_entry
                        active_mic_name = legacy_name
                        active_mic_index = legacy_index
                        active_mic_hostapi = legacy_hostapi
                        AUDIO_DEVICE = active_mic_name
                        AUDIO_DEVICE_HOSTAPI = legacy_hostapi
                        AUDIO_DEVICE_INDEX = legacy_index
                        AUDIO_DEVICE_UID = legacy_uid
                        save_audio_device_to_config(
                            active_mic_name,
                            legacy_hostapi,
                            legacy_index,
                            device_uid=legacy_uid
                        )
                        logger.info(
                            f"Migrated legacy index to identity: '{active_mic_name}' ({legacy_hostapi}) uid={legacy_uid}"
                        )
                        return True
                    device_info = sd.query_devices(legacy_index)
                    if device_info['max_input_channels'] > 0:
                        active_mic_name = device_info['name']
                        active_mic_index = legacy_index
                        hostapi_index = device_info.get('hostapi')
                        hostapi_name = 'Unknown'
                        hostapis = sd.query_hostapis()
                        if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(hostapis):
                            hostapi_name = hostapis[hostapi_index]['name']
                        active_mic_hostapi = hostapi_name
                        legacy_uid = _build_device_uid(active_mic_name, hostapi_name, device_info)
                        AUDIO_DEVICE = active_mic_name
                        AUDIO_DEVICE_HOSTAPI = hostapi_name
                        AUDIO_DEVICE_INDEX = legacy_index
                        AUDIO_DEVICE_UID = legacy_uid
                        save_audio_device_to_config(
                            active_mic_name,
                            hostapi_name,
                            legacy_index,
                            device_uid=legacy_uid
                        )
                        logger.info(
                            f"Migrated legacy index to identity: '{active_mic_name}' ({hostapi_name}) uid={legacy_uid}"
                        )
                        return True
                    logger.warning(f"Legacy device [{AUDIO_DEVICE}] has no input channels, falling back")
                except Exception as e:
                    logger.warning(f"Legacy device index [{AUDIO_DEVICE}] unavailable ({e}), falling back")
            AUDIO_DEVICE = None
            AUDIO_DEVICE_HOSTAPI = None
            AUDIO_DEVICE_INDEX = None
            AUDIO_DEVICE_UID = None
            active_mic_index = None

        # Fall back to system default
        if default_idx is not None:
            default_candidate = device_by_index.get(default_idx)
            if default_candidate is not None:
                active_mic_name, hostapi_name, device_uid = default_candidate
                active_mic_index = default_idx
                active_mic_hostapi = hostapi_name
                AUDIO_DEVICE = active_mic_name
                AUDIO_DEVICE_HOSTAPI = hostapi_name
                AUDIO_DEVICE_INDEX = default_idx
                AUDIO_DEVICE_UID = device_uid
                save_audio_device_to_config(
                    active_mic_name,
                    hostapi_name,
                    default_idx,
                    device_uid=device_uid
                )
                logger.info(
                    f"Using system default device: [{default_idx}] {active_mic_name} ({hostapi_name}) uid={device_uid} (persisted)"
                )
                return True
            logger.warning(f"System default device [{default_idx}] not in input list, falling back")

        # Last resort: first available input device
        first_idx, first_name, first_hostapi, _, first_uid = input_devices[0]
        active_mic_name = first_name
        active_mic_index = first_idx
        active_mic_hostapi = first_hostapi
        AUDIO_DEVICE = active_mic_name
        AUDIO_DEVICE_HOSTAPI = first_hostapi
        AUDIO_DEVICE_INDEX = first_idx
        AUDIO_DEVICE_UID = first_uid
        save_audio_device_to_config(active_mic_name, first_hostapi, first_idx, device_uid=first_uid)
        logger.info(
            f"Falling back to first available input: [{first_idx}] {active_mic_name} ({first_hostapi}) uid={first_uid} (persisted)"
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
    if active_mic_index is not None:
        return active_mic_index
    if isinstance(AUDIO_DEVICE_INDEX, int):
        return AUDIO_DEVICE_INDEX
    return AUDIO_DEVICE


def _current_input_topology_signature(input_devices=None):
    """Return a deterministic signature for currently available input devices."""
    if input_devices is None:
        input_devices = _enumerate_input_devices()

    entries = []
    for idx, name, hostapi_name, _, device_uid in input_devices:
        entries.append((
            str(device_uid or ''),
            str(hostapi_name or ''),
            _normalize_device_name(name),
            int(idx)
        ))
    entries.sort()
    return tuple(entries)


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

    # Release singleton mutex
    _release_single_instance_mutex()

    # Persist clean shutdown marker before hard exit
    _write_runtime_state('shutdown_clean', reason='cleanup_resources')

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


def _atomic_write_text(path, content, encoding='utf-8'):
    """Write text atomically (temp file + replace) to avoid partial config writes."""
    directory = os.path.dirname(path)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding=encoding,
            delete=False,
            dir=directory,
            prefix='config.',
            suffix='.tmp'
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_runtime_state():
    """Read runtime state JSON. Returns {} when missing or invalid."""
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Unable to read runtime state file '{STATE_FILE}': {e}")
    return {}


def _write_runtime_state(status, reason=None, details=None):
    """Atomically persist runtime lifecycle state."""
    state = _read_runtime_state()
    state['status'] = status
    state['updated_at'] = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    state['pid'] = os.getpid()
    if reason is not None:
        state['reason'] = reason
    if details is not None:
        state['details'] = details

    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        payload = json.dumps(state, indent=2, sort_keys=True) + '\n'
        _atomic_write_text(STATE_FILE, payload, encoding='utf-8')
    except Exception as e:
        logger.warning(f"Unable to write runtime state file '{STATE_FILE}': {e}")


def save_audio_device_to_config(device_name, device_hostapi=None, device_index=None, device_uid=None):
    """Persist AUDIO_DEVICE identity to config.py using atomic writes."""
    import re
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')

    if not os.path.exists(config_path):
        logger.warning(f"config.py not found at {config_path}, cannot persist device selection")
        return

    content = None
    source_encoding = None
    for encoding in ('utf-8', 'cp1252'):
        try:
            with open(config_path, 'r', encoding=encoding) as f:
                content = f.read()
            source_encoding = encoding
            break
        except UnicodeDecodeError:
            continue

    if content is None:
        with open(config_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        source_encoding = 'utf-8-replace'
        logger.warning("config.py could not be decoded as UTF-8 or cp1252. Rewriting with replacement chars.")
    elif source_encoding != 'utf-8':
        logger.warning(f"config.py decoded as {source_encoding}; rewriting as UTF-8")

    def format_value(value):
        if value is None:
            return 'None'
        if isinstance(value, int):
            return str(value)
        escaped = str(value).replace("'", "\\'")
        return f"'{escaped}'"

    def upsert_key(text, key, literal_value):
        pattern = rf"(?m)^{re.escape(key)}\s*=.*$"
        replacement = f"{key} = {literal_value}"
        if re.search(pattern, text):
            return re.sub(pattern, replacement, text, count=1)
        if not text.endswith('\n'):
            text += '\n'
        return text + replacement + '\n'

    content = upsert_key(content, 'AUDIO_DEVICE', format_value(device_name))
    content = upsert_key(content, 'AUDIO_DEVICE_HOSTAPI', format_value(device_hostapi))
    content = upsert_key(content, 'AUDIO_DEVICE_INDEX', format_value(device_index))
    content = upsert_key(content, 'AUDIO_DEVICE_UID', format_value(device_uid))

    _atomic_write_text(config_path, content, encoding='utf-8')
    logger.info(
        f"Saved AUDIO_DEVICE identity to config.py: "
        f"name={format_value(device_name)}, hostapi={format_value(device_hostapi)}, "
        f"index={format_value(device_index)}, uid={format_value(device_uid)}"
    )


def switch_audio_device(device_index, device_name, device_hostapi=None, device_uid=None):
    """Switch the audio input to a different device. Hot-swaps the stream.

    Opens the new stream BEFORE closing the old one. If the new stream fails,
    the old stream is kept running. Global state is only updated on success.
    Device is persisted by name, not index.
    """
    global AUDIO_DEVICE, AUDIO_DEVICE_HOSTAPI, AUDIO_DEVICE_INDEX, AUDIO_DEVICE_UID
    global audio_stream, active_mic_name, active_mic_index, active_mic_hostapi

    if is_recording:
        logger.warning("Cannot switch microphone while recording")
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
        AUDIO_DEVICE_HOSTAPI = device_hostapi
        AUDIO_DEVICE_INDEX = device_index
        AUDIO_DEVICE_UID = device_uid
        active_mic_name = device_name
        active_mic_index = device_index
        active_mic_hostapi = device_hostapi

        # Persist device identity to config.py
        save_audio_device_to_config(device_name, device_hostapi, device_index, device_uid=device_uid)

        try:
            last_device_topology_signature = _current_input_topology_signature()
        except Exception:
            pass

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
except Exception as e:
    logger.warning(f"Failed to load config.py ({type(e).__name__}: {e}). Using defaults.")
    HOTKEY = 'alt+f'
    MODEL_SIZE = 'small'
    DEVICE = 'cuda'
    COMPUTE_TYPE = 'float16'
    AUDIO_DEVICE = None
    LANGUAGE = 'en'

# Optional config: persisted audio identity helpers
try:
    from config import AUDIO_DEVICE_HOSTAPI
except Exception:
    AUDIO_DEVICE_HOSTAPI = None

try:
    from config import AUDIO_DEVICE_INDEX
except Exception:
    AUDIO_DEVICE_INDEX = None

try:
    from config import AUDIO_DEVICE_UID
except Exception:
    AUDIO_DEVICE_UID = None

if isinstance(AUDIO_DEVICE_INDEX, str):
    stripped = AUDIO_DEVICE_INDEX.strip()
    if stripped.isdigit():
        AUDIO_DEVICE_INDEX = int(stripped)
    else:
        AUDIO_DEVICE_INDEX = None

if isinstance(AUDIO_DEVICE_UID, str):
    AUDIO_DEVICE_UID = AUDIO_DEVICE_UID.strip() or None

# Optional config: custom vocabulary for better recognition
try:
    from config import VOCABULARY
    if VOCABULARY:
        logger.info(f"Custom vocabulary: {VOCABULARY}")
except Exception:
    VOCABULARY = ''

# Optional config: noise reduction (default off)
try:
    from config import NOISE_REDUCTION
except Exception:
    NOISE_REDUCTION = False

if NOISE_REDUCTION and not NOISEREDUCE_AVAILABLE:
    logger.warning("NOISE_REDUCTION enabled but noisereduce not installed. Disabling.")
    NOISE_REDUCTION = False
elif NOISE_REDUCTION:
    logger.info("Noise reduction enabled")

# Optional config: clipboard copy (default on)
try:
    from config import USE_CLIPBOARD
except Exception:
    USE_CLIPBOARD = True

if USE_CLIPBOARD:
    logger.info("Clipboard copy enabled")

# Optional config: noise gate threshold (minimum RMS level to process audio)
try:
    from config import NOISE_GATE_THRESHOLD
except Exception:
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

    def make_mic_callback(dev_idx, dev_name, dev_hostapi, dev_uid):
        def callback(icon, item):
            switch_audio_device(dev_idx, dev_name, dev_hostapi, dev_uid)
        return callback

    def make_mic_checked(dev_idx):
        def is_checked(item):
            return active_mic_index == dev_idx
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
        test_device = _get_active_stream_device()
        test_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=_test_cb,
            blocksize=1024,
            device=test_device
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


def _reopen_audio_stream(recovery_reason):
    """Try to reopen the current stream target. Returns True on success."""
    global audio_stream, last_callback_time
    update_tray_icon('gray', f'Voice Dictation - Recovering audio ({recovery_reason})')

    try:
        if audio_stream is not None:
            try:
                audio_stream.stop()
                audio_stream.close()
            except Exception:
                pass

        device_to_open = _get_active_stream_device()
        audio_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=audio_callback,
            blocksize=1024,
            device=device_to_open
        )
        audio_stream.start()
        last_callback_time = time.time()
        logger.info(
            f"Audio stream recovered successfully ({recovery_reason}) on open_arg={device_to_open}"
        )
        if model is not None:
            update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
        _write_runtime_state('ready', reason=f'recovered:{recovery_reason}')
        return True
    except Exception as e:
        logger.error(f"Failed to recover audio stream ({recovery_reason}): {e}")
        update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')
        _write_runtime_state('audio_error', reason=f'recovery_failed:{recovery_reason}', details=str(e))
        return False


def stream_health_watchdog():
    """Daemon thread that monitors audio stream health.
    Checks every 5 seconds whether:
    1) Input device topology changed (dock/undock, plug/unplug)
    2) The stream is active and receiving callbacks
    If unhealthy, attempts automatic recovery.
    """
    global audio_stream, last_callback_time, last_device_topology_signature
    logger.info("Stream health watchdog started")

    while True:
        time.sleep(5)

        # Don't interfere while actively recording.
        if is_recording:
            continue

        # Detect device topology changes (dock/undock, device add/remove).
        try:
            input_devices = _enumerate_input_devices()
        except Exception as e:
            logger.warning(f"Failed to enumerate devices in watchdog: {e}")
            input_devices = []

        if input_devices:
            current_signature = _current_input_topology_signature(input_devices)
            if last_device_topology_signature is None:
                last_device_topology_signature = current_signature
            elif current_signature != last_device_topology_signature:
                logger.info("Detected input device topology change. Re-resolving preferred microphone.")
                last_device_topology_signature = current_signature
                previous_index = active_mic_index
                if check_microphone():
                    if tray_icon:
                        tray_icon.update_menu()
                    if audio_stream is None or active_mic_index != previous_index:
                        _reopen_audio_stream('device topology change')
                else:
                    logger.warning("No usable microphone after topology change")
                    update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
                    _write_runtime_state('audio_error', reason='no_microphone_after_topology_change')
                    if audio_stream is not None:
                        try:
                            audio_stream.stop()
                            audio_stream.close()
                        except Exception:
                            pass
                        audio_stream = None
                continue
        else:
            if last_device_topology_signature not in (None, ()):
                logger.warning("No input devices currently available")
            last_device_topology_signature = ()
            update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
            _write_runtime_state('audio_error', reason='no_input_devices')
            if audio_stream is not None:
                try:
                    audio_stream.stop()
                    audio_stream.close()
                except Exception:
                    pass
                audio_stream = None
            continue

        # Recover missing stream when devices exist.
        if audio_stream is None:
            if active_mic_index is None and not check_microphone():
                update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
                continue
            _reopen_audio_stream('stream missing')
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
            _reopen_audio_stream(reason)


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
                f"Opening audio stream on device index={active_mic_index} "
                f"name={AUDIO_DEVICE} open_arg={device_to_open}..."
            )
            audio_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                callback=audio_callback,
                blocksize=1024,
                device=device_to_open
            )
            audio_stream.start()
            logger.info("Audio stream started")
            update_tray_icon('green', f'Voice Dictation - Ready [{HOTKEY.upper()}]')
            _write_runtime_state('ready', reason='startup_complete')

            # Run non-blocking mic test after stream is confirmed working
            test_microphone()

            # Start stream health watchdog daemon
            watchdog_thread = threading.Thread(target=stream_health_watchdog, daemon=True)
            watchdog_thread.start()
        except Exception as e:
            logger.exception(f"Failed to open audio stream: {e}")
            update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')
            _write_runtime_state('audio_error', reason='stream_open_failed', details=str(e))
    else:
        update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
        _write_runtime_state('audio_error', reason='no_usable_microphone')

    # Register hotkey and block (dictation works if stream is active)
    run_dictation_loop()


def main():
    global tray_icon

    try:
        # Ensure only one instance runs
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
