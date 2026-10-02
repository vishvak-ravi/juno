"""Platform-neutral replacements for napsack's two handlers.

napsack's ``InputEventHandler`` and ``ScreenshotHandler`` are the only parts of
its recorder tied to pynput and mss. These classes do the same job, produce
the same ``InputEvent`` and ``BufferImage`` objects, and feed the same napsack
queues, but read from ``capture.sources`` interfaces instead.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable, Optional, Union

import numpy as np
from napsack.record.models import BufferImage, EventType, InputEvent
from napsack.record.workers.screenshot import _resize_by_scale, _resize_if_needed

from capture.sources.base import CursorSource, Monitor, Position, ScreenSource

logger = logging.getLogger(__name__)

# Same names napsack accepts for --disable.
DISABLEABLE_EVENTS = {"move", "scroll", "click", "key"}


class MonitorLocator:
    """Maps a pointer position to a monitor, refreshing the list periodically."""

    def __init__(self, screen: ScreenSource, refresh_interval: float = 5.0):
        self._screen = screen
        self._refresh_interval = refresh_interval
        self._monitors: list[Monitor] = []
        self._last_refresh = float("-inf")

    def monitors(self) -> list[Monitor]:
        now = time.monotonic()
        if not self._monitors or now - self._last_refresh > self._refresh_interval:
            try:
                self._monitors = list(self._screen.monitors())
            except Exception:
                logger.exception("could not list monitors")
            self._last_refresh = now
        return self._monitors

    def locate(self, pos: Optional[Position]) -> Optional[Monitor]:
        """Monitor containing ``pos``; the first monitor if none does or pos is unknown."""
        monitors = self.monitors()
        if not monitors:
            return None
        if pos is not None:
            x, y = int(pos[0]), int(pos[1])
            for monitor in monitors:
                if monitor.contains(x, y):
                    return monitor
        return monitors[0]


class InputHandler:
    """Turns source callbacks into napsack ``InputEvent``s on the event queue.

    Implements ``capture.sources.base.InputListener``.
    """

    def __init__(
        self,
        event_queue,
        screen: ScreenSource,
        cursor: CursorSource,
        disable: Optional[Iterable[str]] = None,
        on_event: Optional[Callable[[InputEvent], None]] = None,
    ):
        self.event_queue = event_queue
        self._cursor = cursor
        self._locator = MonitorLocator(screen)
        self._on_event = on_event
        self._disabled: set[str] = set()
        for name in disable or ():
            if name in DISABLEABLE_EVENTS:
                self._disabled.add(name)
            else:
                logger.warning("unknown event type %r in disable list (valid: %s)",
                               name, sorted(DISABLEABLE_EVENTS))

    def _position(self, pos: Optional[Position]) -> Optional[Position]:
        if pos is None:
            try:
                pos = self._cursor.position()
            except Exception:
                logger.exception("cursor source failed")
                pos = None
        return (int(pos[0]), int(pos[1])) if pos is not None else None

    def _emit(self, event_type: EventType, details: dict,
              pos: Optional[Position], timestamp: Optional[float]) -> None:
        monitor = self._locator.locate(pos)
        event = InputEvent(
            timestamp=time.time() if timestamp is None else timestamp,
            monitor_index=monitor.index if monitor else 0,
            monitor=monitor.to_dict() if monitor else None,
            event_type=event_type,
            details=details,
            cursor_position=pos,
        )
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                logger.exception("on_event hook failed")
        self.event_queue.enqueue(event)

    def on_move(self, pos: Optional[Position] = None,
                timestamp: Optional[float] = None) -> None:
        if "move" in self._disabled:
            return
        pos = self._position(pos)
        x, y = pos if pos else (None, None)
        self._emit(EventType.MOUSE_MOVE, {"x": x, "y": y}, pos, timestamp)

    def on_click(self, button: str, pressed: bool, pos: Optional[Position] = None,
                 timestamp: Optional[float] = None) -> None:
        if "click" in self._disabled:
            return
        pos = self._position(pos)
        x, y = pos if pos else (None, None)
        self._emit(
            EventType.MOUSE_DOWN if pressed else EventType.MOUSE_UP,
            {"x": x, "y": y, "button": str(button)},
            pos, timestamp,
        )

    def on_scroll(self, dx: int, dy: int, pos: Optional[Position] = None,
                  timestamp: Optional[float] = None) -> None:
        if "scroll" in self._disabled:
            return
        pos = self._position(pos)
        x, y = pos if pos else (None, None)
        self._emit(EventType.MOUSE_SCROLL, {"x": x, "y": y, "dx": dx, "dy": dy},
                   pos, timestamp)

    def on_key(self, key: str, pressed: bool,
               timestamp: Optional[float] = None) -> None:
        if "key" in self._disabled:
            return
        self._emit(
            EventType.KEY_PRESS if pressed else EventType.KEY_RELEASE,
            {"key": key},
            self._position(None), timestamp,
        )


class ScreenshotLoop:
    """Fills the image queue with frames of the monitor under the pointer."""

    def __init__(
        self,
        image_queue,
        screen: ScreenSource,
        cursor: CursorSource,
        fps: int = 30,
        max_res: Optional[tuple[int, int]] = None,
        scale: Optional[Union[float, dict[int, float]]] = None,
        target_dpi: Optional[float] = None,
    ):
        """
        Sizing follows napsack: an effective scale below 1.0 wins, otherwise
        ``max_res`` applies, otherwise the frame is kept as captured. ``scale``
        is a fraction or a per-monitor-index dict. When it gives no value for a
        monitor and ``target_dpi`` is set, the scale is ``target_dpi / dpi``
        for monitors that report a physical width.
        """
        self.image_queue = image_queue
        self.fps = fps
        self.interval = 1.0 / fps
        self.max_res = max_res
        self.scale = scale
        self.target_dpi = target_dpi
        self._screen = screen
        self._cursor = cursor
        self._locator = MonitorLocator(screen)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Last (source array, scale inputs) -> resized result, per monitor, so a
        # source that returns the same array while the screen is static costs nothing.
        self._resize_cache: dict[int, tuple[np.ndarray, tuple, np.ndarray, float]] = {}

    def _effective_scale(self, monitor: Monitor) -> Optional[float]:
        if isinstance(self.scale, dict):
            value = self.scale.get(monitor.index)
        else:
            value = self.scale
        if value is None and self.target_dpi is not None and monitor.dpi:
            value = self.target_dpi / monitor.dpi
        return value

    def _resize(self, data: np.ndarray, monitor: Monitor) -> tuple[np.ndarray, float]:
        effective = self._effective_scale(monitor)
        key = (effective, self.max_res)
        cached = self._resize_cache.get(monitor.index)
        if cached is not None and cached[0] is data and cached[1] == key:
            return cached[2], cached[3]

        width = data.shape[1]
        if effective is not None and effective < 1.0:
            resized = _resize_by_scale(data, effective)
        elif self.max_res is not None:
            resized = _resize_if_needed(data, self.max_res)
        else:
            resized = data
        scale_factor = resized.shape[1] / width if width else 1.0
        self._resize_cache[monitor.index] = (data, key, resized, scale_factor)
        return resized, scale_factor

    def capture_once(self) -> Optional[BufferImage]:
        """Grab one frame and enqueue it. Returns the queued image, if any."""
        try:
            pos = self._cursor.position()
        except Exception:
            logger.exception("cursor source failed")
            pos = None
        monitor = self._locator.locate(pos)
        if monitor is None:
            return None

        timestamp = time.time()
        frame = self._screen.grab(monitor.index)
        if frame is None or frame.data is None:
            return None

        data, scale_factor = self._resize(frame.data, frame.monitor)
        image = BufferImage(
            timestamp=timestamp,
            data=data,
            monitor_index=frame.monitor.index,
            scale_factor=scale_factor,
            monitor_dict=frame.monitor.to_dict(),
        )
        self.image_queue.enqueue(image)
        return image

    def _capture_loop(self) -> None:
        while self._running:
            started = time.time()
            try:
                self.capture_once()
            except Exception:
                logger.exception("error capturing screenshot")
            time.sleep(max(0.0, self.interval - (time.time() - started)))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True,
                                        name="capture-screenshots")
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._resize_cache.clear()
