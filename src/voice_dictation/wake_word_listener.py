"""Wake word listener loop for hands-free dictation."""

from __future__ import annotations

import time


def run_wake_word_listener(
    *,
    shutdown_event,
    get_audio_frame,
    predict_fn,
    start_recording,
    stop_and_transcribe,
    record_frame,
    threshold=0.5,
    silence_timeout_s=2.0,
    model_name,
    logger,
    get_time=None,
):
    """Listen for a wake word and trigger recording when detected."""
    if get_time is None:
        get_time = time.time

    logger.info('Wake word listener started (model=%s, threshold=%.2f)', model_name, threshold)

    is_recording = False
    last_speech_time = 0.0

    while not shutdown_event.is_set():
        frame = get_audio_frame()
        if shutdown_event.is_set():
            break

        scores = predict_fn(frame)
        score = scores.get(model_name, 0.0)

        if not is_recording:
            if score >= threshold:
                logger.info('Wake word detected (score=%.3f)', score)
                is_recording = True
                last_speech_time = get_time()
                start_recording()
        else:
            # During recording: capture frames and track silence
            record_frame(frame)
            if score >= threshold:
                # Another wake word during recording — treat as speech activity
                last_speech_time = get_time()
            elif get_time() - last_speech_time >= silence_timeout_s:
                logger.info('Silence timeout reached (%.1fs), ending segment', silence_timeout_s)
                is_recording = False
                stop_and_transcribe()
