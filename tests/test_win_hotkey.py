"""Tests for native Win32 RegisterHotKey hotkey detection."""

import logging
import threading

from voice_dictation.win_hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WM_HOTKEY,
    WM_QUIT,
    WinHotkeyListener,
    parse_hotkey,
)


logger = logging.getLogger('test_win_hotkey')


# ---------------------------------------------------------------------------
# parse_hotkey
# ---------------------------------------------------------------------------

def test_parse_right_ctrl_backslash():
    mods, vk, release_groups = parse_hotkey(['right ctrl', '\\'])
    assert mods == MOD_CONTROL | MOD_NOREPEAT
    assert vk == 0xDC
    # Generic VK_CONTROL: MOD_CONTROL is side-agnostic, so a combo triggered
    # with left ctrl must still count as held.
    assert release_groups == [(0x11,), (0xDC,)]


def test_parse_alt_f():
    mods, vk, release_groups = parse_hotkey(['alt', 'f'])
    assert mods == MOD_ALT | MOD_NOREPEAT
    assert vk == 0x46
    assert release_groups == [(0x12,), (0x46,)]


def test_parse_multiple_modifiers():
    mods, vk, _ = parse_hotkey(['ctrl', 'shift', 'space'])
    assert mods == MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
    assert vk == 0x20


def test_parse_windows_modifier_watches_both_sides():
    _, _, release_groups = parse_hotkey(['windows', 'z'])
    assert (0x5B, 0x5C) in release_groups


def test_parse_bare_non_modifier_key():
    mods, vk, release_groups = parse_hotkey(['f9'])
    assert mods == MOD_NOREPEAT
    assert vk == 0x78
    assert release_groups == [(0x78,)]


def test_parse_rejects_bare_modifier():
    assert parse_hotkey(['right ctrl']) is None


def test_parse_rejects_trailing_modifier():
    assert parse_hotkey(['ctrl', 'shift']) is None


def test_parse_rejects_non_modifier_prefix():
    assert parse_hotkey(['a', 'b']) is None


def test_parse_rejects_unknown_key():
    assert parse_hotkey(['ctrl', 'bogus key']) is None


def test_parse_rejects_empty():
    assert parse_hotkey([]) is None


# ---------------------------------------------------------------------------
# WinHotkeyListener (against a fake Win32 API)
# ---------------------------------------------------------------------------

class FakeUser32:
    def __init__(self, register_ok=True, messages=(), pressed_vks=()):
        self.register_ok = register_ok
        self.register_calls = []
        self.unregister_calls = []
        self.posted_messages = []
        self.pressed_vks = set(pressed_vks)
        self._pending_messages = list(messages)
        self._quit_posted = threading.Event()

    def PeekMessageW(self, msg_ref, hwnd, filter_min, filter_max, flags):
        return 0

    def RegisterHotKey(self, hwnd, hotkey_id, mod_flags, vk):
        self.register_calls.append((hotkey_id, mod_flags, vk))
        return 1 if self.register_ok else 0

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregister_calls.append(hotkey_id)
        return 1

    def GetMessageW(self, msg_ref, hwnd, filter_min, filter_max):
        if self._pending_messages:
            msg_ref._obj.message = self._pending_messages.pop(0)
            return 1
        self._quit_posted.wait(5)
        return 0

    def PostThreadMessageW(self, thread_id, message, wparam, lparam):
        self.posted_messages.append((thread_id, message))
        self._quit_posted.set()
        return 1

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.pressed_vks else 0


class FakeKernel32:
    def GetCurrentThreadId(self):
        return 4242


def _make_listener(fake_user32, *, on_press=None, on_release=None,
                   release_vk_groups=((0x11,), (0xDC,))):
    return WinHotkeyListener(
        MOD_CONTROL | MOD_NOREPEAT,
        0xDC,
        list(release_vk_groups),
        on_press=on_press or (lambda: None),
        on_release=on_release or (lambda: None),
        logger=logger,
        release_poll_interval_s=0.001,
        user32=fake_user32,
        kernel32=FakeKernel32(),
    )


def test_start_registers_hotkey_on_listener_thread():
    fake = FakeUser32()
    listener = _make_listener(fake)
    try:
        assert listener.start() is True
        assert fake.register_calls == [(1, MOD_CONTROL | MOD_NOREPEAT, 0xDC)]
    finally:
        listener.stop()
    assert fake.unregister_calls == [1]
    assert (4242, WM_QUIT) in fake.posted_messages


def test_registration_failure_returns_false_without_unregister():
    fake = FakeUser32(register_ok=False)
    listener = _make_listener(fake)

    assert listener.start() is False

    listener._thread.join(timeout=2)
    assert not listener._thread.is_alive(), 'thread must exit after failed registration'
    assert fake.unregister_calls == [], 'never registered, so never unregistered'


def test_hotkey_message_fires_press_then_release():
    fake = FakeUser32(messages=[WM_HOTKEY], pressed_vks={0x11, 0xDC})
    press_event = threading.Event()
    release_event = threading.Event()

    def on_press():
        press_event.set()
        fake.pressed_vks.clear()  # user releases the combo right after press

    listener = _make_listener(fake, on_press=on_press, on_release=release_event.set)
    try:
        assert listener.start() is True
        assert press_event.wait(2), 'WM_HOTKEY must fire on_press'
        assert release_event.wait(2), 'combo release must fire on_release'
    finally:
        listener.stop()


def test_partial_release_ends_hold():
    # Modifier still down, main key released -> combo no longer held.
    fake = FakeUser32(messages=[WM_HOTKEY], pressed_vks={0x11, 0xDC})
    release_event = threading.Event()

    def on_press():
        fake.pressed_vks.discard(0xDC)

    listener = _make_listener(fake, on_press=on_press, on_release=release_event.set)
    try:
        assert listener.start() is True
        assert release_event.wait(2), 'releasing any combo key must fire on_release'
    finally:
        listener.stop()


def test_stop_during_hold_skips_on_release():
    fake = FakeUser32(messages=[WM_HOTKEY], pressed_vks={0x11, 0xDC})
    press_event = threading.Event()
    release_event = threading.Event()

    listener = _make_listener(fake, on_press=press_event.set, on_release=release_event.set)
    assert listener.start() is True
    assert press_event.wait(2)

    listener.stop()

    assert not listener._thread.is_alive()
    assert not release_event.is_set(), 'shutdown mid-hold must not fire on_release'
    assert fake.unregister_calls == [1]


def test_press_exception_does_not_kill_message_pump():
    fake = FakeUser32(messages=[WM_HOTKEY, WM_HOTKEY])
    release_count = threading.Semaphore(0)

    def on_press():
        raise RuntimeError('boom')

    listener = _make_listener(fake, on_press=on_press, on_release=release_count.release)
    try:
        assert listener.start() is True
        assert release_count.acquire(timeout=2), 'first dispatch must survive on_press failure'
        assert release_count.acquire(timeout=2), 'pump must keep dispatching after a callback error'
    finally:
        listener.stop()


def test_is_combo_held_requires_one_key_down_per_group():
    fake = FakeUser32()
    listener = _make_listener(fake, release_vk_groups=[(0x5B, 0x5C), (0x41,)])

    fake.pressed_vks = {0x5C, 0x41}
    assert listener.is_combo_held() is True, 'either key in a group keeps the group held'

    fake.pressed_vks = {0x5C}
    assert listener.is_combo_held() is False

    fake.pressed_vks = {0x41}
    assert listener.is_combo_held() is False
