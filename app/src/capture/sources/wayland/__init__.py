"""Wayland backend: portal screen capture plus evdev input.

* Screen: xdg-desktop-portal ScreenCast over PipeWire (``portal_screen``).
* Input: ``/dev/input`` through evdev (``evdev_input``).
* Pointer position: cursor metadata on the same ScreenCast stream. The pointer
  is not drawn into the frames; ``annotate.request.draw_cursor`` adds it when
  a screenshot is read.
"""

from __future__ import annotations

from capture.sources.base import Backend
from capture.sources.wayland.evdev_input import EvdevInputSource, check_access
from capture.sources.wayland.portal_screen import PortalScreenSource


def create(fps: int = 5, **options) -> Backend:
    # Fail on input permissions before the desktop shows its share dialog.
    check_access()
    screen = PortalScreenSource(fps=fps)
    return Backend(name="wayland", screen=screen, input=EvdevInputSource(), cursor=screen)
