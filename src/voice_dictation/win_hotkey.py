"""Native Win32 RegisterHotKey-based hotkey detection.

Replaces keyboard-library low-level hook detection for combos that
RegisterHotKey can express. Registration is not a hook: nothing dies at
lock/unlock, no key events are suppressed (the OS delivers modifier
up/down normally, so stuck modifiers are impossible and GetAsyncKeyState
always sees real key state), and our process can never stall the keyboard.

Limitations: RegisterHotKey modifiers are side-agnostic (left ctrl+\\
triggers a right ctrl+\\ registration), and bare-modifier hotkeys
(e.g. 'right ctrl' alone) cannot be registered at all — callers fall
back to keyboard-library hooks for those.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading

from voice_dictation.watchdog_loops import _VK_MAP

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

_HOTKEY_ID = 1

_MOD_FLAGS = {
    'alt': MOD_ALT, 'left alt': MOD_ALT, 'right alt': MOD_ALT,
    'ctrl': MOD_CONTROL, 'left ctrl': MOD_CONTROL, 'right ctrl': MOD_CONTROL,
    'shift': MOD_SHIFT, 'left shift': MOD_SHIFT, 'right shift': MOD_SHIFT,
    'windows': MOD_WIN, 'left windows': MOD_WIN, 'right windows': MOD_WIN,
}

# VKs to watch for release of each modifier flag. Generic VKs (VK_CONTROL,
# VK_SHIFT, VK_MENU) report down when either side is held, matching
# RegisterHotKey's side-agnostic matching — a combo triggered with left ctrl
# must not read as released just because right ctrl is up. The Windows key
# has no generic VK, so both sides are watched.
_MOD_RELEASE_VKS = {
    MOD_ALT: (0x12,),
    MOD_CONTROL: (0x11,),
    MOD_SHIFT: (0x10,),
    MOD_WIN: (0x5B, 0x5C),
}


def parse_hotkey(hotkey_parts):
    """Parse hotkey part names into RegisterHotKey arguments.

    Returns ``(mod_flags, vk, release_vk_groups)`` where *mod_flags* always
    includes MOD_NOREPEAT and *release_vk_groups* is a list of VK tuples —
    the combo counts as held while every group has at least one VK down.

    Returns None when the combo cannot be expressed as a RegisterHotKey
    registration: empty, a bare/trailing modifier, a non-modifier before
    the final key, or an unmappable key name.
    """
    parts = [part.lower().strip() for part in hotkey_parts]
    if not parts:
        return None

    *mod_parts, key_part = parts
    if key_part in _MOD_FLAGS:
        return None
    vk = _VK_MAP.get(key_part)
    if vk is None:
        return None

    mod_flags = MOD_NOREPEAT
    release_vk_groups = []
    for part in mod_parts:
        flag = _MOD_FLAGS.get(part)
        if flag is None:
            return None
        if not mod_flags & flag:
            release_vk_groups.append(_MOD_RELEASE_VKS[flag])
        mod_flags |= flag
    release_vk_groups.append((vk,))
    return mod_flags, vk, release_vk_groups


class WinHotkeyListener:
    """Thread-scoped RegisterHotKey listener with polled release detection.

    RegisterHotKey with a NULL hwnd binds the hotkey to the registering
    thread, so registration, the GetMessageW loop, and unregistration all
    run on one dedicated thread. WM_HOTKEY fires *on_press*, then the same
    thread polls GetAsyncKeyState until any part of the combo releases and
    fires *on_release* (blocking work in on_release only delays the next
    hotkey dispatch, which callers already reject while processing).
    """

    def __init__(
        self,
        mod_flags,
        vk,
        release_vk_groups,
        *,
        on_press,
        on_release,
        logger,
        release_poll_interval_s=0.01,
        user32=None,
        kernel32=None,
    ):
        self._mod_flags = mod_flags
        self._vk = vk
        self._release_vk_groups = release_vk_groups
        self._on_press = on_press
        self._on_release = on_release
        self._logger = logger
        self._release_poll_interval_s = release_poll_interval_s
        self._user32 = user32 if user32 is not None else ctypes.windll.user32
        self._kernel32 = kernel32 if kernel32 is not None else ctypes.windll.kernel32
        self._thread = None
        self._thread_id = None
        self._stop_event = threading.Event()
        self._registration_done = threading.Event()
        self._registration_ok = False

    def start(self, timeout_s=5.0):
        """Start the listener thread. Returns True if RegisterHotKey succeeded.

        False means the combo is already registered by another application
        (or registration timed out) — the thread exits and the caller should
        fall back to another detection path.
        """
        self._thread = threading.Thread(target=self._run, name='win-hotkey', daemon=True)
        self._thread.start()
        self._registration_done.wait(timeout_s)
        return self._registration_ok

    def stop(self, timeout_s=2.0):
        """Unregister the hotkey and stop the listener thread."""
        self._stop_event.set()
        if self._thread_id is not None:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout_s)

    def is_combo_held(self):
        """Return True while every part of the combo is physically held."""
        get_state = self._user32.GetAsyncKeyState
        for vk_group in self._release_vk_groups:
            if not any(get_state(vk) & 0x8000 for vk in vk_group):
                return False
        return True

    def _run(self):
        self._thread_id = self._kernel32.GetCurrentThreadId()
        msg = ctypes.wintypes.MSG()
        # Force-create this thread's message queue so PostThreadMessageW
        # from stop() cannot race registration and be dropped.
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        self._registration_ok = bool(
            self._user32.RegisterHotKey(None, _HOTKEY_ID, self._mod_flags, self._vk)
        )
        self._registration_done.set()
        if not self._registration_ok:
            return
        try:
            while not self._stop_event.is_set():
                result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0 or result == -1:  # WM_QUIT or error
                    break
                if msg.message == WM_HOTKEY:
                    self._handle_hotkey()
        finally:
            self._user32.UnregisterHotKey(None, _HOTKEY_ID)

    def _handle_hotkey(self):
        try:
            self._on_press()
        except Exception:
            self._logger.error('win_hotkey: on_press callback failed', exc_info=True)
        while self.is_combo_held():
            if self._stop_event.wait(self._release_poll_interval_s):
                # Shutting down mid-hold: skip on_release so a dying listener
                # cannot trigger a transcription; the recording-state
                # watchdog's release fallback covers any live recording.
                return
        try:
            self._on_release()
        except Exception:
            self._logger.error('win_hotkey: on_release callback failed', exc_info=True)
