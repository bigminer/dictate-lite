"""Wake word listener loop for hands-free dictation."""

from __future__ import annotations

import time

import numpy as np


def _frame_rms(frame):
    """Compute RMS energy of an int16 audio frame, normalized to 0.0-1.0."""
    float_frame = frame.astype(np.float32) / 32767.0
    return float(np.sqrt(np.mean(float_frame * float_frame)))


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
    speech_energy_threshold=0.01,
    model_name,
    logger,
    get_time=None,
):
    """Listen for a wake word and trigger recording when detected.

    After the wake word fires, recording continues until silence_timeout_s
    of audio below speech_energy_threshold (RMS) is detected.
    """
    if get_time is None:
        get_time = time.time

    logger.info(
        'Wake word listener started (model=%s, threshold=%.2f, '
        'silence=%.1fs, energy=%.4f)',
        model_name, threshold, silence_timeout_s, speech_energy_threshold,
    )

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
            # During recording: capture frames and track speech via audio energy
            record_frame(frame)
            rms = _frame_rms(frame)

            if rms >= speech_energy_threshold:
                # Audio has energy — someone is speaking
                last_speech_time = get_time()
            elif get_time() - last_speech_time >= silence_timeout_s:
                logger.info('Silence timeout reached (%.1fs), ending segment', silence_timeout_s)
                is_recording = False
                stop_and_transcribe()
