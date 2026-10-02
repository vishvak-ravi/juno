"""X11 backend. NOT IMPLEMENTED.

This is Tada's existing path: ``mss`` for frames, ``pynput`` for input and the
pointer position, ``screeninfo`` for monitors. It needs no spike, only an
adapter onto ``capture.sources.base``, and comes after the Wayland backend.
"""

from __future__ import annotations

from capture.sources.base import Backend, BackendUnavailable


def create(**options) -> Backend:
    raise BackendUnavailable(
        "The X11 backend is not implemented yet. "
        "Use --backend fake to exercise the recorder end to end."
    )
