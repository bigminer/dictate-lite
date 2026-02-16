"""Runtime state container for the dictation process."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, RLock


@dataclass
class DictationAppState:
    """Mutable application state guarded by a coarse-grained lock."""

    active_mic_name: str | None = None
    active_mic_index: int | None = None
    active_mic_hostapi: str | None = None

    audio_stream: object | None = None
    last_device_topology_signature: tuple | None = None

    is_recording: bool = False
    is_processing: bool = False
    recorded_frames: list = field(default_factory=list)
    recording_start_time: float = 0.0
    last_callback_time: float = 0.0
    silence_flag: bool = False

    model: object | None = None
    tray_icon: object | None = None

    shutdown_event: Event = field(default_factory=Event)
    lock: RLock = field(default_factory=RLock)
