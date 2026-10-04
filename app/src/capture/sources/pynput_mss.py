"""Sources built on ``mss`` and ``pynput``, the libraries napsack itself uses.

Shared by the ``macos`` and ``x11`` backends. The mapping onto
``capture.sources.base`` follows napsack's ``ScreenshotHandler`` and
``InputEventHandler``, so a session matches what napsack records on the same
machine, with one difference: frames never contain the pointer. As on Wayland,
``annotate.request.draw_cursor`` draws it from ``cursor_position`` when a
screenshot is read.

Importing ``pynput`` on Linux connects to the X display, so the backends import
this module from their ``create`` rather than at the top.

On macOS, ``mss`` returns physical pixels while ``pynput`` reports points
(half as many on a Retina screen). ``Monitor`` geometry is in points, the
space the pointer and click coordinates are in.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import mss
import numpy as np
from pynput import keyboard, mouse
from screeninfo import get_monitors

from capture.sources.base import (
    CursorSource,
    Frame,
    InputListener,
    InputSource,
    Monitor,
    Position,
    ScreenSource,
)

logger = logging.getLogger(__name__)


def key_name(key) -> str:
    """A pynput key as napsack logs it: its character, else ``Key.<name>``.

    napsack logs ``key.char`` even when it is None (a key pynput cannot map to
    a character, such as some keys on macOS); this logs ``str(key)`` then, for
    example ``"<63>"``, so the key is never null.
    """
    char = getattr(key, "char", None)
    return char if char else str(key)


def width_mm_by_geometry() -> dict[tuple[int, int, int, int], int]:
    """Physical widths screeninfo reports, keyed by (left, top, width, height)."""
    try:
        found = get_monitors()
    except Exception:
        logger.exception("could not read physical monitor sizes")
        return {}
    return {(m.x, m.y, m.width, m.height): m.width_mm for m in found if m.width_mm}


def to_monitors(mss_monitors: list[dict],
                width_mm: dict[tuple[int, int, int, int], int]) -> list[Monitor]:
    """mss's monitor list as ``Monitor``s. Entry 0 (all monitors together) is skipped."""
    monitors = []
    for index, m in enumerate(mss_monitors[1:]):
        geometry = (m["left"], m["top"], m["width"], m["height"])
        monitors.append(Monitor(index, *geometry, width_mm=width_mm.get(geometry)))
    return monitors


class MssScreen(ScreenSource):
    """Grabs a monitor with mss each time it is asked.

    The monitor list is read once in ``start``; a monitor plugged in later is
    not seen until the next start.
    """

    def __init__(self):
        self._monitors: list[Monitor] = []
        # mss instances must stay on the thread that created them.
        self._local = threading.local()
        self._instances: list = []
        self._lock = threading.Lock()

    def _mss(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.MSS()
            self._local.sct = sct
            with self._lock:
                self._instances.append(sct)
        return sct

    def start(self) -> None:
        with mss.MSS() as sct:
            self._monitors = to_monitors(sct.monitors, width_mm_by_geometry())
        if not self._monitors:
            logger.warning("mss reported no monitors")

    def stop(self) -> None:
        self._monitors = []
        with self._lock:
            instances, self._instances = self._instances, []
        for sct in instances:
            try:
                sct.close()
            except Exception:
                logger.exception("could not close mss")
        self._local = threading.local()

    def monitors(self) -> list[Monitor]:
        return list(self._monitors)

    def grab(self, monitor_index: int) -> Optional[Frame]:
        monitor = next((m for m in self._monitors if m.index == monitor_index), None)
        if monitor is None:
            return None
        shot = self._mss().grab(monitor.to_dict())
        # BGRA -> RGB, as napsack's capture_screenshot does.
        data = np.asarray(shot)[:, :, [2, 1, 0]]
        return Frame(data=data, monitor=monitor)


def safely(name: str, callback: Callable, *args) -> None:
    """Call ``callback``, logging instead of raising: pynput stops a listener whose callback raises."""
    try:
        callback(*args)
    except Exception:
        logger.exception("%s callback failed", name)


class PynputInput(InputSource):
    """Global mouse and keyboard events from pynput's listeners."""

    def __init__(self):
        self._mouse: Optional[mouse.Listener] = None
        self._keyboard: Optional[keyboard.Listener] = None

    def start(self, listener: InputListener) -> None:
        # Fixed signatures on purpose: pynput passes a callback that takes
        # *args every argument it has, including an extra ``injected`` flag.
        def on_move(x, y):
            safely("move", listener.on_move, (int(x), int(y)))

        def on_click(x, y, button, pressed):
            safely("click", listener.on_click, str(button), pressed, (int(x), int(y)))

        def on_scroll(x, y, dx, dy):
            safely("scroll", listener.on_scroll, dx, dy, (int(x), int(y)))

        def on_press(key):
            safely("key", listener.on_key, key_name(key), True)

        def on_release(key):
            safely("key", listener.on_key, key_name(key), False)

        self._mouse = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        self._keyboard = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._mouse.start()
        self._keyboard.start()

    def stop(self) -> None:
        for listener in (self._mouse, self._keyboard):
            if listener is not None:
                listener.stop()
        self._mouse = self._keyboard = None


class PynputCursor(CursorSource):
    """The pointer position from ``pynput.mouse.Controller``, one controller per thread."""

    def __init__(self):
        self._local = threading.local()

    def position(self) -> Optional[Position]:
        controller = getattr(self._local, "controller", None)
        if controller is None:
            controller = mouse.Controller()
            self._local.controller = controller
        x, y = controller.position
        return int(x), int(y)
