"""Backend selection."""

from __future__ import annotations

import importlib
import os
from typing import Mapping

from capture.sources.base import (
    Backend,
    BackendUnavailable,
    CursorSource,
    Frame,
    InputListener,
    InputSource,
    Monitor,
    Position,
    ScreenSource,
    UnknownCursor,
)

__all__ = [
    "Backend", "BackendUnavailable", "CursorSource", "Frame", "InputListener",
    "InputSource", "Monitor", "Position", "ScreenSource", "UnknownCursor",
    "BACKENDS", "detect_backend_name", "load_backend",
]

# name -> module exposing ``create(**options) -> Backend``
BACKENDS: dict[str, str] = {
    "wayland": "capture.sources.wayland",
    "x11": "capture.sources.x11",
    "fake": "capture.sources.fake",
}


def detect_backend_name(env: Mapping[str, str] | None = None) -> str:
    """Pick a backend from the session environment."""
    env = os.environ if env is None else env
    session = env.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland" or (not session and env.get("WAYLAND_DISPLAY")):
        return "wayland"
    if session == "x11" or (not session and env.get("DISPLAY")):
        return "x11"
    raise BackendUnavailable(
        "Could not detect a display session (XDG_SESSION_TYPE, WAYLAND_DISPLAY "
        "and DISPLAY are all unset). Set backend in the config explicitly."
    )


def load_backend(name: str = "auto", **options) -> Backend:
    """Build the named backend, or the detected one for ``auto``."""
    if name == "auto":
        name = detect_backend_name()
    module_path = BACKENDS.get(name)
    if module_path is None:
        raise BackendUnavailable(
            f"Unknown backend '{name}'. Available: {', '.join(sorted(BACKENDS))}"
        )
    module = importlib.import_module(module_path)
    return module.create(**options)
