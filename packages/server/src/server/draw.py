"""The recorder of one connected ``/draw`` page, run inside the server.

A tablet cannot run ``python -m capture``, so the server runs the same
``Recorder`` for it on a ``capture.sources.pushed`` backend that the page's
socket (``server.routes.draw``) feeds. Sessions are written under
``juno.config.draw_sessions_dir`` and sent to this server's own
``PUT /events`` by the same ``AggregationUploader`` a desktop recorder uses, so
they are stored, annotated and listed like any other device's.

Like ``capture.__main__``, it reports to the control file every
``control.poll_seconds`` under ``draw.device_id`` and records only while that
device's switch is on: off ends the session, on starts a new one.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from capture.recorder import Recorder, new_session_dir
from capture.sources.base import Backend
from capture.upload import AggregationUploader
from juno.config import CaptureConfig
from server.control import Report, record_report

logger = logging.getLogger(__name__)


class DrawRecorder:

    def __init__(self, backend: Backend, capture: CaptureConfig, poll_seconds: float,
                 control_file: Path, sessions: Path, events_url: str, device_id: str):
        """
        Args:
            backend: a ``capture.sources.pushed`` backend the page's socket feeds.
            capture: frame rate, buffer and screenshot sizing, as for a desktop recorder.
            sessions: the directory the sessions are written under.
            events_url: this server's events collection, e.g. ``http://127.0.0.1:8765/events``.
        """
        self.backend = backend
        self.capture = capture
        self.poll_seconds = poll_seconds
        self.control_file = control_file
        self.sessions = sessions
        self.events_url = events_url
        self.device_id = device_id
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="draw-recorder")

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        """End the session, send what is left of it, and stop reporting."""
        self._done.set()
        self._thread.join()

    def _report(self, session_id: Optional[str]) -> bool:
        report = Report(capture="running" if session_id else "paused", session_id=session_id)
        return record_report(self.control_file, self.device_id, report)

    def _run(self) -> None:
        recorder: Optional[Recorder] = None
        uploader: Optional[AggregationUploader] = None
        try:
            while True:
                wanted = self._report(uploader.session_id if uploader else None)
                if wanted and recorder is None:
                    recorder = Recorder(
                        self.backend,
                        session_dir=new_session_dir(self.sessions),
                        fps=self.capture.fps,
                        buffer_seconds=self.capture.buffer_seconds,
                        target_dpi=self.capture.dpi,
                        scale=self.capture.scale,
                        max_res=self.capture.max_res,
                        save_screenshots=self.capture.screenshots,
                        disable=self.capture.disable,
                    )
                    uploader = AggregationUploader(recorder.session_dir, self.events_url,
                                                   self.device_id)
                    recorder.on_aggregation = uploader.on_aggregation
                    recorder.start()
                elif recorder is not None and not wanted:
                    stop(recorder, uploader)
                    recorder = uploader = None
                if self._done.wait(self.poll_seconds):
                    break
        except Exception:
            logger.exception("the %s recorder failed", self.device_id)
        finally:
            if recorder is not None:
                stop(recorder, uploader)
            self._report(None)


def stop(recorder: Recorder, uploader: AggregationUploader) -> None:
    if not recorder.stop():
        logger.warning("some events of %s were not captured in any aggregation",
                       recorder.session_dir)
    uploader.close()
    logger.info("draw session saved to %s (%d aggregations)",
                recorder.session_dir, recorder.processed_aggregations)
