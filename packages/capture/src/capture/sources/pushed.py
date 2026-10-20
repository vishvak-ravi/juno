"""A backend fed by its caller: frames and input pushed in, not read from this machine.

The server's ``/draw`` route records a tablet's drawing page with it (see
``server.draw``): the page sends its canvas and pointer events, the route
pushes them here, and the ``Recorder`` turns them into napsack's session
files exactly as it does for a desktop backend. It is not in ``BACKENDS``:
without a caller pushing, it records nothing.

The page's one canvas is monitor 0 at (0, 0); coordinates are its pixels.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from capture.sources.base import (
    Backend,
    CursorSource,
    Frame,
    InputListener,
    InputSource,
    Monitor,
    Position,
    ScreenSource,
)


class PushedScreen(ScreenSource):
    """Hands out the last frame pushed, the same array until another arrives."""

    def __init__(self, monitor: Monitor):
        self._monitor = monitor
        self._frame: Optional[np.ndarray] = None
        self._started = False

    def push(self, data: np.ndarray) -> None:
        """Make ``data`` (H x W x 3, uint8, RGB, the monitor's size) what the screen shows."""
        self._frame = data

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def monitors(self) -> list[Monitor]:
        return [self._monitor] if self._started else []

    def grab(self, monitor_index: int) -> Optional[Frame]:
        if not self._started or self._frame is None or monitor_index != self._monitor.index:
            return None
        return Frame(data=self._frame, monitor=self._monitor)


class PushedCursor(CursorSource):
    """The position of the last pointer event pushed."""

    def __init__(self):
        self.last: Optional[Position] = None

    def position(self) -> Optional[Position]:
        return self.last


class PushedInput(InputSource):
    """Passes pushed pointer events to the recorder's listener while started."""

    def __init__(self, cursor: PushedCursor):
        self._cursor = cursor
        self._listener: Optional[InputListener] = None
        self._lock = threading.Lock()

    def start(self, listener: InputListener) -> None:
        with self._lock:
            self._listener = listener

    def stop(self) -> None:
        with self._lock:
            self._listener = None

    def click(self, pressed: bool, pos: Position) -> None:
        self._cursor.last = pos
        with self._lock:
            if self._listener is not None:
                self._listener.on_click("Button.left", pressed, pos)

    def move(self, pos: Position) -> None:
        self._cursor.last = pos
        with self._lock:
            if self._listener is not None:
                self._listener.on_move(pos)


def create(width: int, height: int) -> Backend:
    """A backend whose one monitor is ``width`` x ``height`` pixels."""
    cursor = PushedCursor()
    return Backend(name="pushed",
                   screen=PushedScreen(Monitor(index=0, left=0, top=0, width=width, height=height)),
                   input=PushedInput(cursor),
                   cursor=cursor)
