"""Wayland backend: portal screen capture plus evdev input.

* Screen: xdg-desktop-portal ScreenCast over PipeWire (``portal_screen``).
* Input: ``/dev/input`` through evdev (``evdev_input``).
* Pointer position: not available. Wayland gives clients no global pointer
  position, so events carry ``cursor_position: null`` and screenshots come
  from the first shared monitor. The pointer is still drawn into each frame.
"""

from __future__ import annotations

from capture.sources.base import Backend, UnknownCursor
from capture.sources.evdev_input import EvdevInputSource, check_access
from capture.sources.portal_screen import PortalScreenSource


def create(fps: int = 5, **options) -> Backend:
    # Fail on input permissions before the desktop shows its share dialog.
    check_access()
    return Backend(
        name="wayland",
        screen=PortalScreenSource(fps=fps),
        input=EvdevInputSource(),
        cursor=UnknownCursor(),
    )
