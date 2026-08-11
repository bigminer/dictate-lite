"""Recording/transcription pipeline helpers used by dictate.py."""

from __future__ import annotations

import time


def begin_processing_from_recording(state, logger):
    """Transition recording -> processing and return captured frames."""
    with state.lock:
        if not state.is_recording:
            return None
        state.is_recording = False
        if state.is_processing:
            logger.debug('stop_recording ignored: transcription already in progress')
            return None
        state.is_processing = True
        recorded_frames = state.recorded_frames
        state.recorded_frames = []
    return recorded_frames


def prepare_audio_for_transcription(
    recorded_frames,
    *,
    np_module,
    sample_rate,
    noise_gate_threshold,
    noise_gate_peak_multiplier,
    noise_reduction,
    noise_reducer_module,
    logger,
):
    """Validate and normalize captured frames for transcription."""
    if not recorded_frames:
        logger.info('No audio captured')
        return None

    try:
        audio_data = np_module.concatenate(recorded_frames, axis=0)
    except (ValueError, TypeError) as exc:
        logger.error(f'Failed to concatenate audio frames (corrupt data?): {exc}')
        return None

    duration_s = len(audio_data) / sample_rate
    if duration_s < 0.1:
        logger.info(f'Audio too short ({duration_s:.3f}s < 0.1s), skipping transcription')
        return None

    if noise_gate_threshold > 0:
        power = float(np_module.mean(audio_data * audio_data))
        rms = power ** 0.5
        if power < (noise_gate_threshold * noise_gate_threshold):
            peak = float(np_module.max(np_module.abs(audio_data)))
            peak_gate = noise_gate_threshold * noise_gate_peak_multiplier
            if peak < peak_gate:
                logger.info(
                    f'Audio too quiet (RMS={rms:.4f} < {noise_gate_threshold}, '
                    f'peak={peak:.4f} < {peak_gate:.4f}), skipping'
                )
                return None
            logger.info(
                f'Audio RMS below gate but peak indicates speech '
                f'(RMS={rms:.4f}, peak={peak:.4f} >= {peak_gate:.4f}); continuing'
            )

    if noise_reduction:
        logger.debug('Applying noise reduction...')
        audio_data = noise_reducer_module.reduce_noise(y=np_module.ravel(audio_data), sr=sample_rate)
    return audio_data


def transcribe_audio(
    audio_data,
    *,
    model,
    transcription_io_module,
    sample_rate,
    transcribe_language,
    vocabulary,
    max_typed_chars,
    logger,
):
    """Transcribe audio and return normalized text payload with timing metadata."""
    transcribe_opts = {
        'beam_size': 5,
        'language': transcribe_language,
    }
    if vocabulary:
        transcribe_opts['initial_prompt'] = vocabulary

    t0 = time.perf_counter()
    raw_text = transcription_io_module.transcribe_audio_array(
        model,
        audio_data,
        **transcribe_opts,
    )
    transcription_ms = (time.perf_counter() - t0) * 1000
    text = transcription_io_module.sanitize_transcript_text(raw_text)
    if len(text) > max_typed_chars:
        logger.warning(
            f'Transcript length {len(text)} exceeds MAX_TYPED_CHARS={max_typed_chars}; truncating output'
        )
        text = text[:max_typed_chars].rstrip()

    audio_duration_s = len(audio_data) / sample_rate
    realtime_factor = (transcription_ms / 1000.0) / audio_duration_s
    logger.info(
        f'Transcription complete: {transcription_ms:.0f}ms for {audio_duration_s:.1f}s audio '
        f'(rtf={realtime_factor:.2f}), {len(text)} chars'
    )

    return {
        'raw_text': raw_text,
        'text': text,
        'transcription_ms': transcription_ms,
    }

