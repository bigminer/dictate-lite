"""Tests for wake word listener loop."""

import logging
import threading
import time

import numpy as np

from voice_dictation.wake_word_listener import run_wake_word_listener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(value=0.0):
    """Create a 1280-sample int16 frame (80ms at 16kHz)."""
    return (np.ones(1280, dtype=np.float32) * value * 32767).astype(np.int16)


SILENT_FRAME = _make_frame(0.0)


def _run_listener(*, frames, predict_results, start_recording_fn=None,
                  stop_and_transcribe_fn=None, record_frame=None,
                  shutdown=None, **overrides):
    """Convenience wrapper with sane defaults."""
    if shutdown is None:
        shutdown = threading.Event()

    frame_idx = 0
    fake_time = [0.0]  # mutable, advances 80ms per frame (simulates real-time)

    def get_frame():
        nonlocal frame_idx
        fake_time[0] += 0.08  # 80ms per frame at 16kHz/1280 samples
        if frame_idx < len(frames):
            f = frames[frame_idx]
            frame_idx += 1
            return f
        shutdown.set()
        return SILENT_FRAME

    predict_idx = 0

    def predict_fn(frame):
        nonlocal predict_idx
        if predict_idx < len(predict_results):
            r = predict_results[predict_idx]
            predict_idx += 1
            return r
        return {'test_model': 0.0}

    start_calls = []
    stop_calls = []

    kwargs = dict(
        shutdown_event=shutdown,
        get_audio_frame=get_frame,
        predict_fn=predict_fn,
        start_recording=start_recording_fn or (lambda: start_calls.append(1)),
        stop_and_transcribe=stop_and_transcribe_fn or (lambda: stop_calls.append(1)),
        record_frame=record_frame or (lambda f: None),
        threshold=0.5,
        silence_timeout_s=0.15,  # ~2 silent frames at 80ms each
        model_name='test_model',
        logger=logging.getLogger('test_wake_word'),
        get_time=lambda: fake_time[0],
    )
    kwargs.update(overrides)
    run_wake_word_listener(**kwargs)
    return start_calls, stop_calls


# ---------------------------------------------------------------------------
# Cycle 1: Wake word detection triggers recording
# ---------------------------------------------------------------------------

def test_wake_word_detected_triggers_recording():
    # 5 silent frames, then 1 frame with high wake word score
    frames = [SILENT_FRAME] * 5 + [SILENT_FRAME]
    predict_results = [
        {'test_model': 0.0},
        {'test_model': 0.0},
        {'test_model': 0.0},
        {'test_model': 0.0},
        {'test_model': 0.0},
        {'test_model': 0.9},  # wake word detected
    ]

    start_calls, _ = _run_listener(frames=frames, predict_results=predict_results)

    assert len(start_calls) == 1, (
        f'Expected start_recording called once on wake word detection, '
        f'got {len(start_calls)} calls'
    )


# ---------------------------------------------------------------------------
# Cycle 2: Silence after speech triggers transcription
# ---------------------------------------------------------------------------

def test_silence_after_speech_triggers_transcription():
    # Wake word detected, then enough silent frames to exceed silence_timeout_s.
    # With silence_timeout_s=0.15 and 80ms frames, 2 frames of silence (160ms) triggers.
    frames = [
        SILENT_FRAME,      # pre-wake silence
        SILENT_FRAME,      # wake word fires here
        SILENT_FRAME,      # post-wake silence frame 1 (80ms elapsed)
        SILENT_FRAME,      # post-wake silence frame 2 (160ms > 150ms timeout)
    ]
    predict_results = [
        {'test_model': 0.0},
        {'test_model': 0.9},  # wake word
        {'test_model': 0.0},  # silence during recording
        {'test_model': 0.0},  # silence continues → timeout
    ]

    start_calls, stop_calls = _run_listener(
        frames=frames, predict_results=predict_results,
    )

    assert len(start_calls) == 1, f'Expected 1 start call, got {len(start_calls)}'
    assert len(stop_calls) == 1, (
        f'Expected stop_and_transcribe called once after silence timeout, '
        f'got {len(stop_calls)} calls'
    )


# ---------------------------------------------------------------------------
# Cycle 3: Below-threshold scores don't trigger recording
# ---------------------------------------------------------------------------

def test_ignores_audio_below_threshold():
    """Regression guard: low scores should never trigger start_recording."""
    frames = [SILENT_FRAME] * 5
    predict_results = [
        {'test_model': 0.1},
        {'test_model': 0.3},
        {'test_model': 0.49},  # just below 0.5 threshold
        {'test_model': 0.2},
        {'test_model': 0.0},
    ]

    start_calls, stop_calls = _run_listener(
        frames=frames, predict_results=predict_results,
    )

    assert len(start_calls) == 0, (
        f'start_recording should not be called for sub-threshold scores, '
        f'got {len(start_calls)} calls'
    )
    assert len(stop_calls) == 0, (
        f'stop_and_transcribe should not be called without prior activation, '
        f'got {len(stop_calls)} calls'
    )


# ---------------------------------------------------------------------------
# Cycle 4: Wake word frames excluded from recording
# ---------------------------------------------------------------------------

def test_wake_word_frames_excluded_from_recording():
    """Frames containing the wake word should not be passed to record_frame.

    Only frames AFTER the wake word detection should be recorded.
    """
    speech_frame = _make_frame(0.5)  # distinguishable from silence
    frames = [
        SILENT_FRAME,      # pre-wake (not recorded)
        SILENT_FRAME,      # wake word fires here (not recorded — it IS the wake word)
        speech_frame,       # post-wake speech (SHOULD be recorded)
        speech_frame,       # more speech (SHOULD be recorded)
        SILENT_FRAME,       # silence → timeout
        SILENT_FRAME,       # silence → timeout triggers
    ]
    predict_results = [
        {'test_model': 0.0},
        {'test_model': 0.9},  # wake word
        {'test_model': 0.0},
        {'test_model': 0.0},
        {'test_model': 0.0},
        {'test_model': 0.0},
    ]

    recorded_frames = []

    start_calls, stop_calls = _run_listener(
        frames=frames, predict_results=predict_results,
        record_frame=lambda f: recorded_frames.append(f),
    )

    assert len(recorded_frames) == 2, (
        f'Expected 2 speech frames recorded (excluding wake word frame), '
        f'got {len(recorded_frames)}'
    )
    # Verify the recorded frames are the speech frames, not the wake word frame
    for f in recorded_frames:
        assert np.allclose(f, speech_frame), 'Recorded frame should be speech, not silence/wake word'


# ---------------------------------------------------------------------------
# Cycle 5: Listener respects shutdown event
# ---------------------------------------------------------------------------

def test_listener_respects_shutdown_event():
    """Listener should exit promptly when shutdown_event is set."""
    shutdown = threading.Event()
    call_count = 0

    def get_frame():
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            shutdown.set()
        return SILENT_FRAME

    def predict_fn(frame):
        return {'test_model': 0.0}

    run_wake_word_listener(
        shutdown_event=shutdown,
        get_audio_frame=get_frame,
        predict_fn=predict_fn,
        start_recording=lambda: None,
        stop_and_transcribe=lambda: None,
        record_frame=lambda f: None,
        model_name='test_model',
        logger=logging.getLogger('test_wake_word'),
    )

    # If we get here, the loop exited. Verify it didn't run forever.
    assert call_count >= 3, f'Expected at least 3 frame reads, got {call_count}'
    assert call_count <= 5, f'Expected loop to stop promptly, got {call_count} frame reads'
