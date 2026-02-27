"""Wake word mode state management."""

from __future__ import annotations


class WakeWordMode:
    """Tracks whether wake word listening is enabled."""

    def __init__(self):
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def toggle(self) -> None:
        self._enabled = not self._enabled
