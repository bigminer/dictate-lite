"""Tests for hotkey registration: bare single keys use hook_key, combos use add_hotkey."""

from types import SimpleNamespace

import dictate


def test_single_key_uses_hook_key(monkeypatch):
    calls = {'hook_key': [], 'add_hotkey': []}
    monkeypatch.setattr(
        dictate.keyboard, 'hook_key',
        lambda key, callback, suppress=False: calls['hook_key'].append((key, suppress)),
    )
    monkeypatch.setattr(
        dictate.keyboard, 'add_hotkey',
        lambda *a, **kw: calls['add_hotkey'].append((a, kw)),
    )
    monkeypatch.setattr(dictate, 'HOTKEY', 'right ctrl')
    monkeypatch.setattr(dictate, 'HOTKEY_PARTS', ['right ctrl'])

    dictate._register_hotkey()

    assert calls['hook_key'] == [('right ctrl', True)], 'bare key must be registered via hook_key with suppress'
    assert not calls['add_hotkey'], 'add_hotkey never fires callbacks for a lone modifier'


def test_combo_uses_add_hotkey_pair(monkeypatch):
    calls = {'hook_key': [], 'add_hotkey': []}
    monkeypatch.setattr(
        dictate.keyboard, 'hook_key',
        lambda key, callback, suppress=False: calls['hook_key'].append((key, suppress)),
    )
    monkeypatch.setattr(
        dictate.keyboard, 'add_hotkey',
        lambda *a, **kw: calls['add_hotkey'].append((a, kw)),
    )
    monkeypatch.setattr(dictate, 'HOTKEY', 'alt+f')
    monkeypatch.setattr(dictate, 'HOTKEY_PARTS', ['alt', 'f'])

    dictate._register_hotkey()

    assert len(calls['add_hotkey']) == 2, 'combos keep the press/release add_hotkey pair'
    assert not calls['hook_key']


def test_hotkey_event_dispatches_press_and_release(monkeypatch):
    pressed, released = [], []
    monkeypatch.setattr(dictate, 'on_hotkey_press', lambda: pressed.append(1))
    monkeypatch.setattr(dictate, 'on_hotkey_release', lambda: released.append(1))

    dictate._on_hotkey_event(SimpleNamespace(event_type=dictate.keyboard.KEY_DOWN))
    assert pressed and not released

    dictate._on_hotkey_event(SimpleNamespace(event_type=dictate.keyboard.KEY_UP))
    assert released
