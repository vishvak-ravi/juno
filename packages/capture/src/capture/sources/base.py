"""Interfaces the recorder depends on.

A platform backend supplies three things: screen frames, input events, and the
cursor position. Everything above this file (handlers, recorder, checker) is
platform neutral and talks only to these interfaces.

Conventions every implementation must follow, because napsack's aggregation
and labeling code downstream assumes them:

* Timestamps are wall-clock epoch seconds (``time.time()``).
* Coordinates are global desktop pixels, in the same space as
  ``Monitor.left/top/width/height``.
* Key names use pynput's format: a printable key is its character (``"a"``,
  ``"A"``, ``"!"``), anything else is ``"Key.<name>"`` (``"Key.enter"``,
  ``"Key.shift"``, ``"Key.cmd"``).
* Button names use pynput's format: ``"Button.left"``, ``"Button.right"``,
  ``"Button.middle"``.
* Scroll deltas are pynput's: ``dy > 0`` is up, ``dx > 0`` is right.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

Position = tuple[int, int]


class BackendUnavailable(RuntimeError):
    """The requested backend cannot run here (missing, unimplemented, or denied)."""


@dataclass(frozen=True)
class Monitor:
    """One physical output. ``index`` is stable for the life of the source."""

    index: int
    left: int
    top: int
    width: int
    height: int
    width_mm: Optional[int] = None

    def contains(self, x: int, y: int) -> bool:
        return (self.left <= x < self.left + self.width
                and self.top <= y < self.top + self.height)

    def to_dict(self) -> dict:
        """The monitor dict napsack stores on events and aggregations."""
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}

    @property
    def dpi(self) -> Optional[float]:
        if not self.width_mm or self.width_mm <= 0:
            return None
        return self.width / (self.width_mm / 25.4)


@dataclass
class Frame:
    """The current contents of one monitor."""

    data: np.ndarray  # H x W x 3, uint8, RGB
    monitor: Monitor


class ScreenSource(ABC):
    """Produces frames of a monitor on demand."""

    @abstractmethod
    def start(self) -> None:
        """Acquire whatever is needed to grab frames. May prompt the user once."""

    @abstractmethod
    def stop(self) -> None:
        """Release everything acquired by ``start``. Safe to call twice."""

    @abstractmethod
    def monitors(self) -> list[Monitor]:
        """Monitors currently available. Empty until the source is ready."""

    @abstractmethod
    def grab(self, monitor_index: int) -> Optional[Frame]:
        """Return what ``monitor_index`` shows right now, or None if unavailable.

        Called at the capture rate (5 fps by default) from one thread. A source
        that receives frames only when the screen changes should return its
        most recent frame, and may return the *same array object* while nothing
        has changed: the caller uses identity to skip redundant resizing. The
        returned array must not be mutated afterwards.
        """


class CursorSource(ABC):
    """Reports where the pointer is."""

    @abstractmethod
    def position(self) -> Optional[Position]:
        """Global pointer position, or None when the platform cannot say.

        None is a supported answer: events are then logged with
        ``cursor_position: null`` and attributed to the first monitor.
        """


class UnknownCursor(CursorSource):
    """For platforms that cannot report the pointer position."""

    def position(self) -> Optional[Position]:
        return None


class InputListener(Protocol):
    """What an ``InputSource`` calls. Mirrors pynput's listener callbacks.

    ``pos`` and ``timestamp`` are optional. When a source omits them the
    listener fills them in from the ``CursorSource`` and the wall clock.
    """

    def on_move(self, pos: Optional[Position] = None,
                timestamp: Optional[float] = None) -> None: ...

    def on_click(self, button: str, pressed: bool, pos: Optional[Position] = None,
                 timestamp: Optional[float] = None) -> None: ...

    def on_scroll(self, dx: int, dy: int, pos: Optional[Position] = None,
                  timestamp: Optional[float] = None) -> None: ...

    def on_key(self, key: str, pressed: bool,
               timestamp: Optional[float] = None) -> None: ...


class InputSource(ABC):
    """Delivers global keyboard and mouse events to a listener."""

    @abstractmethod
    def start(self, listener: InputListener) -> None:
        """Begin delivering events. Callbacks may arrive on any thread."""

    @abstractmethod
    def stop(self) -> None:
        """Stop delivering events. Safe to call twice."""


@dataclass
class Backend:
    """The three sources for one platform, as built by a backend's ``create``."""

    name: str
    screen: ScreenSource
    input: InputSource
    cursor: CursorSource
