"""Watchdog and recovery loops for dictation runtime."""

from __future__ import annotations

import ctypes
import sys
import time


def run_recording_state_watchdog(
    *,
    state,
    shutdown_event,
    get_recording_snapshot,
    is_hotkey_currently_pressed,
    stop_recording_and_transcribe,
    set_recording_icon,
    recording_muted_warning_title,
    recording_monitor_interval,
    idle_recording_monitor_interval,
    max_recording_seconds,
    release_fallback_message,
    logger,
):
    """Monitor timeout/silence and fallback key-release detection while recording."""
    silence_warned = False
    while not shutdown_event.is_set():
        is_recording, recording_start_time, silence_flag = get_recording_snapshot()

        if is_recording and recording_start_time > 0:
            if not is_hotkey_currently_pressed():
                logger.info(release_fallback_message)
                silence_warned = False
                stop_recording_and_transcribe()
                shutdown_event.wait(recording_monitor_interval)
                continue

            elapsed = time.time() - recording_start_time
            if elapsed > max_recording_seconds:
                logger.warning(f'Recording timeout after {max_recording_seconds}s - force stopping')
                silence_warned = False
                stop_recording_and_transcribe()
                shutdown_event.wait(recording_monitor_interval)
                continue

            if silence_flag and not silence_warned:
                silence_warned = True
                set_recording_icon(recording_muted_warning_title)
            elif not silence_flag and silence_warned:
                silence_warned = False
                set_recording_icon()
        else:
            silence_warned = False

        wait_seconds = recording_monitor_interval if is_recording else idle_recording_monitor_interval
        shutdown_event.wait(wait_seconds)


def run_microphone_self_test(
    *,
    manager,
    get_active_stream_device,
    capture_from_stream_fn,
    sd_module,
    sample_rate,
    np_module,
    logger,
    set_ready_icon,
    ready_muted_warning_title,
):
    """Record a short clip and warn if the active microphone appears muted."""
    if not manager.is_active:
        logger.warning('test_microphone: audio stream not active, skipping test')
        return

    logger.info('Running microphone self-test (0.5s capture)...')

    try:
        test_device = get_active_stream_device()
        test_audio = capture_from_stream_fn(
            sd_module,
            device_index=test_device,
            seconds=0.5,
            sample_rate=sample_rate,
            channels=1,
            dtype='float32',
            blocksize=1024,
            logger=logger,
        )
    except Exception as exc:
        logger.warning(f'Microphone self-test failed to capture audio: {exc}')
        return

    if test_audio.size == 0:
        logger.warning('Microphone self-test: no frames captured')
        return

    try:
        rms = np_module.sqrt(np_module.mean(test_audio ** 2))
    except Exception as exc:
        logger.warning(f'Microphone self-test: error computing RMS: {exc}')
        return

    if rms < 1e-6:
        logger.warning(f'Microphone self-test: RMS={rms:.8f} - mic may be muted or disconnected')
        set_ready_icon(ready_muted_warning_title)
    else:
        logger.info(f'Microphone self-test passed (RMS={rms:.6f})')


def reopen_audio_stream(
    *,
    recovery_reason,
    switch_lock,
    update_tray_icon,
    get_active_stream_device,
    get_stream_manager,
    state,
    logger,
    set_ready_icon,
    write_runtime_state,
):
    """Try to reopen the current stream target. Returns True on success."""
    if not switch_lock.acquire(blocking=False):
        logger.info(f'Skipping stream recovery ({recovery_reason}): switch already in progress')
        return False

    update_tray_icon('gray', f'Voice Dictation - Recovering audio ({recovery_reason})')

    try:
        device_to_open = get_active_stream_device()
        manager = get_stream_manager()
        manager.reopen(device_to_open)
        state.audio_stream = manager.stream
        state.last_callback_time = time.time()
        logger.info(
            f'Audio stream recovered successfully ({recovery_reason}) on open_arg={device_to_open}'
        )
        if state.model is not None:
            set_ready_icon()
        write_runtime_state('ready', reason=f'recovered:{recovery_reason}')
        return True
    except Exception as exc:
        logger.error(f'Failed to recover audio stream ({recovery_reason}): {exc}')
        update_tray_icon('gray', 'Voice Dictation - Audio error (see log)')
        write_runtime_state('audio_error', reason=f'recovery_failed:{recovery_reason}', details=str(exc))
        return False
    finally:
        switch_lock.release()


def run_stream_health_watchdog(
    *,
    state,
    shutdown_event,
    is_audio_pipeline_busy,
    enumerate_input_devices,
    current_input_topology_signature,
    check_microphone,
    reopen_audio_stream_fn,
    update_tray_icon,
    write_runtime_state,
    get_stream_manager,
    logger,
    watchdog_poll_s=5,
    backoff_max_s=300,
    re_resolve_after_failures=3,
    on_persistent_failure=None,
    on_recovery=None,
    alert_after_failures=5,
):
    """Monitor stream/device health and attempt automatic recovery.

    *on_persistent_failure* is invoked once per failure streak when
    *alert_after_failures* consecutive recovery attempts have failed;
    *on_recovery* is invoked when a reopen finally succeeds after that alert.
    """
    logger.info('Stream health watchdog started')

    consecutive_failures = 0
    next_retry_time = 0.0
    alerted = False

    def _on_recovery_failure():
        """Shared handler for reopen failures: backoff, then re-resolve after threshold."""
        nonlocal consecutive_failures, next_retry_time, alerted
        consecutive_failures += 1
        backoff_s = min(watchdog_poll_s * (2 ** consecutive_failures), backoff_max_s)
        next_retry_time = time.time() + backoff_s
        logger.warning(
            f'Recovery failed ({consecutive_failures} consecutive). '
            f'Next retry in {backoff_s}s'
        )
        if consecutive_failures >= alert_after_failures and not alerted:
            alerted = True
            if on_persistent_failure:
                try:
                    on_persistent_failure(consecutive_failures)
                except Exception as exc:
                    logger.error(f'on_persistent_failure callback failed: {exc}')
        if consecutive_failures >= re_resolve_after_failures:
            logger.info('Multiple consecutive recovery failures. Re-resolving device...')
            if check_microphone():
                consecutive_failures = 0
                next_retry_time = 0.0
                logger.info('Device re-resolved. Will retry immediately.')

    def _on_recovery_success():
        """Shared handler for reopen successes: reset backoff, clear any alert."""
        nonlocal consecutive_failures, next_retry_time, alerted
        consecutive_failures = 0
        next_retry_time = 0.0
        if alerted:
            alerted = False
            if on_recovery:
                try:
                    on_recovery()
                except Exception as exc:
                    logger.error(f'on_recovery callback failed: {exc}')

    while not shutdown_event.is_set():
        shutdown_event.wait(watchdog_poll_s)
        if shutdown_event.is_set():
            break

        if is_audio_pipeline_busy():
            continue

        try:
            input_devices = enumerate_input_devices()
        except Exception as exc:
            logger.warning(f'Failed to enumerate devices in watchdog: {exc}')
            input_devices = []

        if input_devices:
            current_signature = current_input_topology_signature(input_devices)
            if state.last_device_topology_signature is None:
                state.last_device_topology_signature = current_signature
            elif current_signature != state.last_device_topology_signature:
                logger.info('Detected input device topology change. Re-resolving preferred microphone.')
                state.last_device_topology_signature = current_signature
                consecutive_failures = 0
                next_retry_time = 0.0
                previous_index = state.active_mic_index
                if check_microphone():
                    if state.tray_icon:
                        state.tray_icon.update_menu()
                    if state.audio_stream is None or state.active_mic_index != previous_index:
                        if reopen_audio_stream_fn('device topology change'):
                            _on_recovery_success()
                else:
                    logger.warning('No usable microphone after topology change')
                    update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
                    write_runtime_state('audio_error', reason='no_microphone_after_topology_change')
                    get_stream_manager().close()
                    state.audio_stream = None
                continue
        else:
            if state.last_device_topology_signature not in (None, ()):
                logger.warning('No input devices currently available')
            state.last_device_topology_signature = ()
            update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
            write_runtime_state('audio_error', reason='no_input_devices')
            get_stream_manager().close()
            state.audio_stream = None
            continue

        manager = get_stream_manager()
        if manager.stream is None:
            if state.active_mic_index is None and not check_microphone():
                update_tray_icon('gray', 'Voice Dictation - No microphone (see log)')
                continue
            if time.time() < next_retry_time:
                continue
            if reopen_audio_stream_fn('stream missing'):
                _on_recovery_success()
            else:
                _on_recovery_failure()
            continue

        stream_active = manager.is_active
        callback_stale = False
        if state.last_callback_time > 0:
            callback_stale = (time.time() - state.last_callback_time) > 10

        if not stream_active or callback_stale:
            reason = 'stream inactive' if not stream_active else 'no callbacks for >10s'
            logger.error(f'Audio stream appears dead ({reason}). Attempting recovery...')
            if reopen_audio_stream_fn(reason):
                _on_recovery_success()
            else:
                _on_recovery_failure()


# ---------------------------------------------------------------------------
# Virtual-key codes for GetAsyncKeyState (Windows only)
# ---------------------------------------------------------------------------

_VK_MAP = {
    'alt': 0x12, 'left alt': 0xA4, 'right alt': 0xA5,
    'ctrl': 0x11, 'left ctrl': 0xA2, 'right ctrl': 0xA3,
    'shift': 0x10, 'left shift': 0xA0, 'right shift': 0xA1,
    'windows': 0x5B, 'left windows': 0x5B, 'right windows': 0x5C,
    'space': 0x20, 'enter': 0x0D, 'tab': 0x09, 'escape': 0x1B,
    'backspace': 0x08, 'delete': 0x2E, 'insert': 0x2D,
    'home': 0x24, 'end': 0x23, 'page up': 0x21, 'page down': 0x22,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    'caps lock': 0x14, 'num lock': 0x90, 'scroll lock': 0x91,
    'print screen': 0x2C, 'pause': 0x13,
}

# F1-F24
for _i in range(1, 25):
    _VK_MAP[f'f{_i}'] = 0x6F + _i  # VK_F1=0x70 .. VK_F24=0x87

# Single characters (a-z, 0-9)
for _c in range(ord('a'), ord('z') + 1):
    _VK_MAP[chr(_c)] = _c - 32  # VK for 'a' is 0x41
for _d in range(0, 10):
    _VK_MAP[str(_d)] = 0x30 + _d


def _hotkey_parts_to_vk_codes(hotkey_parts):
    """Convert hotkey part names to Windows virtual key codes.

    Returns a list of VK codes, or None if any part is unmappable.
    """
    codes = []
    for part in hotkey_parts:
        key = part.lower().strip()
        vk = _VK_MAP.get(key)
        if vk is None:
            return None
        codes.append(vk)
    return codes


def _are_all_vk_pressed(vk_codes):
    """Check physical key state via Win32 GetAsyncKeyState.

    Returns True if every key in *vk_codes* is currently held down.
    """
    if sys.platform != 'win32':
        return False
    get_state = ctypes.windll.user32.GetAsyncKeyState
    for vk in vk_codes:
        # Bit 15 (0x8000) means key is currently held down
        if not (get_state(vk) & 0x8000):
            return False
    return True


def run_keyboard_hook_watchdog(
    *,
    state,
    shutdown_event,
    hotkey_parts,
    rehook_fn,
    logger,
    start_recording_fn=None,
    on_hook_suspect=None,
    poll_interval_s=0.1,
    recording_start_threshold_s=0.15,
    detection_threshold_s=1.0,
    proactive_rehook_interval_s=600,
):
    """Detect and recover from silently-dead Windows keyboard hooks.

    Three strategies:
    1. **Direct recording**: Uses GetAsyncKeyState to detect when the hotkey is
       physically held but no callback fires.  After *recording_start_threshold_s*
       the watchdog starts recording directly, bypassing the dead hook.
    2. **Reactive rehook**: If the keys are held for *detection_threshold_s*,
       the hook is assumed dead and re-registered.
    3. **Proactive rehook**: Every *proactive_rehook_interval_s* seconds the
       hotkeys are unconditionally re-registered as cheap insurance.

    *on_hook_suspect* is invoked with a reason string whenever strategy 1 or 2
    fires — i.e. whenever there is direct evidence the hook is dead.
    """

    def _notify_hook_suspect(reason):
        if on_hook_suspect is None:
            return
        try:
            on_hook_suspect(reason)
        except Exception as exc:
            logger.error(f'Keyboard hook watchdog: on_hook_suspect callback failed: {exc}')
    if sys.platform != 'win32':
        logger.info('Keyboard hook watchdog: not Windows, skipping')
        return

    vk_codes = _hotkey_parts_to_vk_codes(hotkey_parts)
    if vk_codes is None:
        logger.warning(
            f'Keyboard hook watchdog: could not map hotkey parts {hotkey_parts!r} '
            f'to VK codes; reactive detection disabled'
        )

    logger.info(
        f'Keyboard hook watchdog started '
        f'(reactive={"enabled" if vk_codes else "disabled"}, '
        f'proactive every {proactive_rehook_interval_s}s)'
    )

    pressed_since = 0.0
    poll_triggered_recording = False
    last_proactive_rehook = time.time()

    while not shutdown_event.is_set():
        shutdown_event.wait(poll_interval_s)
        if shutdown_event.is_set():
            break

        now = time.time()

        # --- Proactive rehook ---
        if (now - last_proactive_rehook) >= proactive_rehook_interval_s:
            logger.info('Keyboard hook watchdog: proactive rehook (scheduled)')
            try:
                rehook_fn()
                state.hotkey_rehook_count += 1
                last_proactive_rehook = now
                pressed_since = 0.0
            except Exception as exc:
                logger.error(f'Keyboard hook watchdog: proactive rehook failed: {exc}')
            continue

        # --- Reactive detection ---
        if vk_codes is None:
            continue

        # Skip if the app is busy (recording or processing) — either the
        # hook worked or the poll already triggered recording.
        if state.is_recording or state.is_processing:
            pressed_since = 0.0
            poll_triggered_recording = False
            continue

        all_pressed = _are_all_vk_pressed(vk_codes)

        if all_pressed:
            if pressed_since == 0.0:
                pressed_since = now
            else:
                held_s = now - pressed_since

                # Start recording directly if hook isn't firing
                if (start_recording_fn
                        and held_s >= recording_start_threshold_s
                        and not poll_triggered_recording):
                    logger.info(
                        'Keyboard hook watchdog: hotkey detected via polling '
                        f'({held_s:.2f}s held), starting recording directly'
                    )
                    try:
                        start_recording_fn()
                        poll_triggered_recording = True
                    except Exception as exc:
                        logger.error(f'Keyboard hook watchdog: start recording failed: {exc}')
                    _notify_hook_suspect(
                        f'hotkey held {held_s:.2f}s with no callback; recording started via polling'
                    )

                # Rehook after longer hold
                if held_s >= detection_threshold_s:
                    logger.warning(
                        f'Keyboard hook watchdog: hotkey held for '
                        f'{held_s:.1f}s with no callback — rehooking'
                    )
                    try:
                        rehook_fn()
                        state.hotkey_rehook_count += 1
                        last_proactive_rehook = now
                    except Exception as exc:
                        logger.error(f'Keyboard hook watchdog: reactive rehook failed: {exc}')
                    _notify_hook_suspect(
                        f'hotkey held {held_s:.1f}s with no callback; hooks re-registered'
                    )
                    pressed_since = 0.0
        else:
            pressed_since = 0.0
            poll_triggered_recording = False

