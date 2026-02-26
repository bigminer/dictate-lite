"""Watchdog and recovery loops for dictation runtime."""

from __future__ import annotations

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
):
    """Monitor stream/device health and attempt automatic recovery."""
    logger.info('Stream health watchdog started')

    consecutive_failures = 0
    next_retry_time = 0.0

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
                        reopen_audio_stream_fn('device topology change')
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
                consecutive_failures = 0
                next_retry_time = 0.0
            else:
                consecutive_failures += 1
                backoff_s = min(watchdog_poll_s * (2 ** consecutive_failures), backoff_max_s)
                next_retry_time = time.time() + backoff_s
                logger.warning(
                    f'Recovery failed ({consecutive_failures} consecutive). '
                    f'Next retry in {backoff_s}s'
                )
            continue

        stream_active = manager.is_active
        callback_stale = False
        if state.last_callback_time > 0:
            callback_stale = (time.time() - state.last_callback_time) > 10

        if not stream_active or callback_stale:
            reason = 'stream inactive' if not stream_active else 'no callbacks for >10s'
            logger.error(f'Audio stream appears dead ({reason}). Attempting recovery...')
            if reopen_audio_stream_fn(reason):
                consecutive_failures = 0
                next_retry_time = 0.0
            else:
                consecutive_failures += 1
                backoff_s = min(watchdog_poll_s * (2 ** consecutive_failures), backoff_max_s)
                next_retry_time = time.time() + backoff_s
                logger.warning(
                    f'Recovery failed ({consecutive_failures} consecutive). '
                    f'Next retry in {backoff_s}s'
                )

