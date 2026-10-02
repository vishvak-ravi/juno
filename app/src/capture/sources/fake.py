"""Synthetic backend: generated frames and scripted input.

Lets the whole recorder run with no display access, for tests and for
``backend: fake`` in the config.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable, Optional, Sequence

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

DEFAULT_MONITORS = (Monitor(index=0, left=0, top=0, width=640, height=360),)

# (seconds to wait first, callback name, positional args)
ScriptStep = tuple[float, str, tuple]

DEFAULT_SCRIPT: tuple[ScriptStep, ...] = (
    (1.0, "on_click", ("Button.left", True)),
    (0.08, "on_click", ("Button.left", False)),
    (0.5, "on_key", ("h", True)), (0.05, "on_key", ("h", False)),
    (0.1, "on_key", ("i", True)), (0.05, "on_key", ("i", False)),
    (0.1, "on_key", ("Key.enter", True)), (0.05, "on_key", ("Key.enter", False)),
    (1.0, "on_scroll", (0, -1)),
    (0.1, "on_scroll", (0, -1)),
    (2.0, "on_move", ()),
)


class FakeScreenSource(ScreenSource):
    """Frames filled with a colour that changes every ``change_every`` grabs.

    Between changes it hands back the same array, like a damage-driven source.
    """

    def __init__(self, monitors: Sequence[Monitor] = DEFAULT_MONITORS,
                 change_every: int = 5):
        self._monitors = list(monitors)
        self._change_every = max(1, change_every)
        self._started = False
        self._frames: dict[int, np.ndarray] = {}
        self.grabs: dict[int, int] = {m.index: 0 for m in self._monitors}

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def monitors(self) -> list[Monitor]:
        return list(self._monitors) if self._started else []

    def grab(self, monitor_index: int) -> Optional[Frame]:
        if not self._started:
            return None
        monitor = next((m for m in self._monitors if m.index == monitor_index), None)
        if monitor is None:
            return None
        count = self.grabs[monitor_index]
        self.grabs[monitor_index] = count + 1
        if count % self._change_every == 0 or monitor_index not in self._frames:
            step = count // self._change_every
            colour = (40 + (step * 37) % 200, 60 + monitor_index * 80, 200 - (step * 23) % 150)
            data = np.empty((monitor.height, monitor.width, 3), dtype=np.uint8)
            data[:] = colour
            self._frames[monitor_index] = data
        return Frame(data=self._frames[monitor_index], monitor=monitor)


class FakeCursor(CursorSource):
    """A pointer the test or script places by hand."""

    def __init__(self, position: Optional[Position] = (10, 10)):
        self._position = position

    def set(self, position: Optional[Position]) -> None:
        self._position = position

    def position(self) -> Optional[Position]:
        return self._position


class ScriptedInputSource(InputSource):
    """Replays a list of listener calls on a background thread."""

    def __init__(self, script: Iterable[ScriptStep] = DEFAULT_SCRIPT, loop: bool = True):
        self._script = list(script)
        self._loop = loop
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, listener: InputListener) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(listener,), daemon=True)
        self._thread.start()

    def _run(self, listener: InputListener) -> None:
        while not self._stop.is_set():
            for delay, name, args in self._script:
                if self._stop.wait(delay):
                    return
                getattr(listener, name)(*args)
            if not self._loop:
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)


def create(**options) -> Backend:
    return Backend(
        name="fake",
        screen=FakeScreenSource(),
        input=ScriptedInputSource(),
        cursor=FakeCursor(),
    )
