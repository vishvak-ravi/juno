"""X11 backend: Tada's path, mss frames and pynput input and pointer.

Not yet run on an X11 session.
"""

from __future__ import annotations

from capture.sources.base import Backend


def create(**options) -> Backend:
    # Imported here: importing pynput connects to the X display, which must not
    # happen merely because this module was loaded.
    from capture.sources.pynput_mss import MssScreen, PynputCursor, PynputInput

    return Backend(name="x11", screen=MssScreen(), input=PynputInput(), cursor=PynputCursor())
