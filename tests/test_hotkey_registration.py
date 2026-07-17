"""Tests for hotkey registration: native RegisterHotKey first, keyboard-lib fallback."""

from types import SimpleNamespace

import dictate
from voice_dictation import win_hotkey


def _capture_keyboard_calls(monkeypatch):
    calls = {'hook_key': [], 'add_hotkey': []}
    monkeypatch.setattr(
        dictate.keyboard, 'hook_key',
        lambda key, callback, suppress=False: calls['hook_key'].append((key, suppress)),
    )
    monkeypatch.setattr(
        dictate.keyboard, 'add_hotkey',
        lambda *a, **kw: calls['add_hotkey'].append((a, kw)),
    )
    return calls


def test_combo_registers_via_native_win32_hotkey(monkeypatch):
    calls = _capture_keyboard_calls(monkeypatch)
    created = []

    class FakeListener:
        def __init__(self, mod_flags, vk, release_vk_groups, **kwargs):
            created.append((mod_flags, vk, release_vk_groups))

        def start(self):
            return True

    monkeypatch.setattr(dictate.win_hotkey, 'WinHotkeyListener', FakeListener)
    monkeypatch.setattr(dictate, '_win_hotkey_listener', None)
    monkeypatch.setattr(dictate, 'HOTKEY', 'right ctrl+\\')
    monkeypatch.setattr(dictate, 'HOTKEY_PARTS', ['right ctrl', '\\'])

    dictate._register_hotkey()

    assert created == [(win_hotkey.MOD_CONTROL | win_hotkey.MOD_NOREPEAT, 0xDC, [(0x11,), (0xDC,)])]
    assert dictate._win_hotkey_listener is not None, 'native listener must become the active path'
    assert not calls['hook_key'] and not calls['add_hotkey'], 'no keyboard hooks on the native path'


def test_rejected_registration_alerts_and_falls_back_to_keyboard_hooks(monkeypatch):
    calls = _capture_keyboard_calls(monkeypatch)

    class FakeListener:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return False  # combo owned by another app

    tones = []
    monkeypatch.setattr(dictate.win_hotkey, 'WinHotkeyListener', FakeListener)
    monkeypatch.setattr(dictate, '_win_hotkey_listener', None)
    monkeypatch.setattr(dictate, '_play_error_tone', lambda: tones.append(1))
    monkeypatch.setattr(dictate, '_last_hook_alert_time', 0.0)
    monkeypatch.setattr(dictate.STATE, 'hook_degraded', False)
    monkeypatch.setattr(dictate.STATE, 'model', None)
    monkeypatch.setattr(dictate, 'HOTKEY', 'alt+f')
    monkeypatch.setattr(dictate, 'HOTKEY_PARTS', ['alt', 'f'])

    dictate._register_hotkey()

    assert dictate._win_hotkey_listener is None
    assert dictate.STATE.hook_degraded is True, 'rejection must latch the degraded state'
    assert tones == [1], 'rejection must be loud'
    assert len(calls['add_hotkey']) == 2, 'fallback keeps the press/release add_hotkey pair'
    assert not calls['hook_key']


def test_bare_modifier_uses_hook_key_fallback(monkeypatch):
    calls = _capture_keyboard_calls(monkeypatch)
    monkeypatch.setattr(dictate, '_win_hotkey_listener', None)
    monkeypatch.setattr(dictate, 'HOTKEY', 'right ctrl')
    monkeypatch.setattr(dictate, 'HOTKEY_PARTS', ['right ctrl'])

    dictate._register_hotkey()

    assert dictate._win_hotkey_listener is None, 'bare modifiers cannot use RegisterHotKey'
    assert calls['hook_key'] == [('right ctrl', True)], 'bare key must be registered via hook_key with suppress'
    assert not calls['add_hotkey'], 'add_hotkey never fires callbacks for a lone modifier'


def test_hotkey_event_dispatches_press_and_release(monkeypatch):
    pressed, released = [], []
    monkeypatch.setattr(dictate, 'on_hotkey_press', lambda: pressed.append(1))
    monkeypatch.setattr(dictate, 'on_hotkey_release', lambda: released.append(1))

    dictate._on_hotkey_event(SimpleNamespace(event_type=dictate.keyboard.KEY_DOWN))
    assert pressed and not released

    dictate._on_hotkey_event(SimpleNamespace(event_type=dictate.keyboard.KEY_UP))
    assert released


def test_rehook_skipped_while_pipeline_busy(monkeypatch):
    order = []
    monkeypatch.setattr(dictate, '_is_audio_pipeline_busy', lambda: True)
    monkeypatch.setattr(dictate, '_stop_win_hotkey_listener', lambda: order.append('stop'))
    monkeypatch.setattr(dictate.keyboard, 'unhook_all', lambda: order.append('unhook'))
    monkeypatch.setattr(dictate, '_register_hotkey', lambda: order.append('register'))

    dictate._rehook_hotkeys()

    assert order == [], 'mid-recording re-registration would cut the dictation short'


def test_rehook_stops_native_listener_before_reregistering(monkeypatch):
    order = []
    monkeypatch.setattr(dictate, '_is_audio_pipeline_busy', lambda: False)
    monkeypatch.setattr(dictate, '_stop_win_hotkey_listener', lambda: order.append('stop'))
    monkeypatch.setattr(dictate.keyboard, 'unhook_all', lambda: order.append('unhook'))
    monkeypatch.setattr(dictate, '_register_hotkey', lambda: order.append('register'))

    dictate._rehook_hotkeys()

    assert order == ['stop', 'unhook', 'register']


def _stop_recording_capturing_modifier_cleanup(monkeypatch, listener):
    cleanup_calls = []
    monkeypatch.setattr(dictate, '_win_hotkey_listener', listener)
    monkeypatch.setattr(dictate, '_begin_processing_from_recording', lambda: [object()])
    monkeypatch.setattr(dictate, '_release_all_modifiers_sendinput', lambda: cleanup_calls.append(1))
    monkeypatch.setattr(dictate, '_set_processing_icon', lambda: None)
    monkeypatch.setattr(dictate, '_prepare_audio_for_transcription', lambda frames: None)
    monkeypatch.setattr(dictate, '_finish_processing_cycle', lambda: None)

    dictate.stop_recording_and_transcribe()
    return cleanup_calls


def test_keyboard_path_clears_modifiers_at_recording_stop(monkeypatch):
    assert _stop_recording_capturing_modifier_cleanup(monkeypatch, None) == [1]


def test_native_path_skips_modifier_cleanup_at_recording_stop(monkeypatch):
    # RegisterHotKey never suppresses key-ups, so OS modifier state is
    # already correct — synthetic releases would desync a held Ctrl.
    assert _stop_recording_capturing_modifier_cleanup(monkeypatch, object()) == []
