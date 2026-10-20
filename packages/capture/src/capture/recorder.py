"""The recorder: napsack's queues and workers wired to pluggable sources.

This mirrors ``napsack.record.__main__.ScreenRecorder`` and Tada's
``OnlineRecorder`` (5 fps, 120 s buffer, 100 dpi frames, mouse moves dropped,
sessions under ``<data>/screen/sessions/session_<timestamp>``). It does not
import either, because both pull in pynput and mss at import time.

Files written per session, all by napsack's own code:

* ``events.jsonl``           every input event, as napsack writes it (Python repr)
* ``input_events.jsonl``     the same events as JSON
* ``raw_aggregations.jsonl`` one record per burst boundary, with its events
* ``screenshots/*.png`` and ``screenshots.jsonl``
"""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

from napsack.record.constants import constants_manager
from napsack.record.models import AggregationConfig, EventQueue, ImageQueue
from napsack.record.models.aggregation import ProcessedAggregation
from napsack.record.workers import AggregationWorker, SaveWorker

from capture.handlers import InputHandler, ScreenshotLoop
from capture.sources.base import Backend
from juno.config import sessions_dir

logger = logging.getLogger(__name__)

# Tada's OnlineRecorder defaults.
DEFAULT_FPS = 5
DEFAULT_BUFFER_SECONDS = 120
DEFAULT_TARGET_DPI = 100
DEFAULT_DISABLE = ("move",)


def new_session_dir(sessions: Path) -> Path:
    """A session directory named for the time now, under the directory of sessions ``sessions``."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return sessions / f"session_{stamp}"


class Recorder:

    def __init__(
        self,
        backend: Backend,
        data_dir: Union[str, Path, None] = None,
        *,
        session_dir: Union[str, Path, None] = None,
        fps: int = DEFAULT_FPS,
        buffer_seconds: int = DEFAULT_BUFFER_SECONDS,
        target_dpi: Optional[float] = DEFAULT_TARGET_DPI,
        scale: Optional[Union[float, dict[int, float]]] = None,
        max_res: Optional[tuple[int, int]] = None,
        save_screenshots: bool = True,
        disable: Optional[Iterable[str]] = DEFAULT_DISABLE,
        poll_interval: float = 1.0,
        on_aggregation: Optional[Callable[[ProcessedAggregation], None]] = None,
    ):
        """
        Args:
            backend: the platform sources.
            data_dir: base directory; the session goes under ``screen/sessions``.
            session_dir: exact session directory, overriding ``data_dir``.
            on_aggregation: called with each processed aggregation, screenshot
                still attached in memory. This is where a labeler plugs in.
        """
        if session_dir is None:
            if data_dir is None:
                raise ValueError("pass data_dir or session_dir")
            session_dir = new_session_dir(sessions_dir(Path(data_dir)))
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.backend = backend
        self.fps = fps
        self.buffer_seconds = buffer_seconds
        self.on_aggregation = on_aggregation
        self.running = False
        self.processed_aggregations = 0

        constants = constants_manager.get()
        self.image_queue = ImageQueue(max_length=fps * buffer_seconds)
        self.event_queue = EventQueue(
            image_queue=self.image_queue,
            click_config=AggregationConfig(constants.CLICK_GAP_THRESHOLD,
                                           constants.CLICK_TOTAL_THRESHOLD),
            move_config=AggregationConfig(constants.MOVE_GAP_THRESHOLD,
                                          constants.MOVE_TOTAL_THRESHOLD),
            scroll_config=AggregationConfig(constants.SCROLL_GAP_THRESHOLD,
                                            constants.SCROLL_TOTAL_THRESHOLD),
            key_config=AggregationConfig(constants.KEY_GAP_THRESHOLD,
                                         constants.KEY_TOTAL_THRESHOLD),
            poll_interval=poll_interval,
            session_dir=self.session_dir,
        )
        self.save_worker = SaveWorker(self.session_dir, False,
                                      save_screenshots=save_screenshots)
        self.aggregation_worker = AggregationWorker(
            event_queue=self.event_queue, save_worker=self.save_worker)
        self.event_queue.set_callback(self._on_aggregation_request)

        self.screenshot_loop = ScreenshotLoop(
            image_queue=self.image_queue,
            screen=backend.screen,
            cursor=backend.cursor,
            fps=fps,
            max_res=max_res,
            scale=scale,
            target_dpi=target_dpi,
        )
        self.input_handler = InputHandler(
            self.event_queue,
            screen=backend.screen,
            cursor=backend.cursor,
            disable=disable,
            on_event=self.save_worker.save_input_event,
        )

    def _on_aggregation_request(self, request) -> None:
        if not request:
            return
        # napsack reports problems with print(); keep stdout clean for callers.
        with contextlib.redirect_stdout(sys.stderr):
            processed = self.aggregation_worker.process_aggregation(request)
        self.processed_aggregations += 1
        logger.info("aggregation %s: %d events, screenshot %s",
                    request.reason, len(processed.events),
                    "yes" if processed.screenshot is not None else "no")
        if self.on_aggregation is not None:
            try:
                self.on_aggregation(processed)
            except Exception:
                logger.exception("on_aggregation hook failed")

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        logger.info("recording to %s (%s backend, %d fps, %d s buffer)",
                    self.session_dir, self.backend.name, self.fps, self.buffer_seconds)
        try:
            self.backend.screen.start()
            self.screenshot_loop.start()
            self.event_queue.start()
            # Let frames exist before the first event asks for one.
            time.sleep(constants_manager.get().PADDING_BEFORE / 1000.0)
            self.backend.input.start(self.input_handler)
        except BaseException:
            self.running = False
            self.screenshot_loop.stop()
            self.event_queue.stop()
            with contextlib.suppress(Exception):
                self.backend.screen.stop()
            raise

    def stop(self) -> bool:
        """Stop, flush every open burst to disk, and report whether no event was lost."""
        if not self.running:
            return True
        self.running = False

        self.backend.input.stop()
        # Keep frames coming long enough for the final burst's end screenshot.
        time.sleep(constants_manager.get().PADDING_AFTER / 1000.0
                   + self.screenshot_loop.interval)
        self.screenshot_loop.stop()
        self.event_queue.stop()
        with contextlib.redirect_stdout(sys.stderr):
            self.event_queue.process_all_remaining()
            complete = self.aggregation_worker.validate_events_processed()
        self.backend.screen.stop()

        logger.info("session saved to %s (%d aggregations)",
                    self.session_dir, self.processed_aggregations)
        return complete
