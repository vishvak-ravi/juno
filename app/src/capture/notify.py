"""Tells the server when a chunk of the session is on disk and can be annotated.

The recorder calls ``ChunkNotifier.on_aggregation`` after each aggregation is
written. Each time another ``chunk_size`` rows of ``raw_aggregations.jsonl``
are complete, the notifier sends ``PUT <annotations_url>/<session id>-<chunk>``
from its own thread, so recording never waits on the server. A request that
fails is logged and dropped; the same PUT can be repeated later by anyone.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

AGGREGATIONS_FILE = "raw_aggregations.jsonl"


class ChunkNotifier:

    def __init__(self, session_dir: Path, annotations_url: str, chunk_size: int,
                 timeout_seconds: float = 10.0):
        """
        Args:
            annotations_url: the server's annotations collection, e.g.
                ``http://127.0.0.1:8765/annotations``.
            chunk_size: rows per chunk; must be the server's ``annotate.chunk_size``.
        """
        self.session_dir = Path(session_dir)
        self.annotations_url = annotations_url.rstrip("/")
        self.chunk_size = chunk_size
        self.timeout_seconds = timeout_seconds
        self._offset = 0  # bytes of the aggregations file already counted
        self._rows = 0
        self._notified = 0  # chunks already announced
        self._chunks: queue.Queue[Optional[int]] = queue.Queue()
        self._thread = threading.Thread(target=self._send, daemon=True, name="chunk-notifier")
        self._thread.start()

    def on_aggregation(self, processed=None) -> None:
        """Count the rows written since the last call and announce each chunk they complete."""
        try:
            with open(self.session_dir / AGGREGATIONS_FILE, "rb") as rows:
                rows.seek(self._offset)
                added = rows.read()
        except FileNotFoundError:
            return
        # A row counts once its newline is written.
        complete = added[:added.rfind(b"\n") + 1]
        self._offset += len(complete)
        self._rows += complete.count(b"\n")
        while (self._notified + 1) * self.chunk_size <= self._rows:
            self._chunks.put(self._notified)
            self._notified += 1

    def close(self, timeout_seconds: float = 15.0) -> None:
        """Send what is queued, then stop."""
        self._chunks.put(None)
        self._thread.join(timeout=timeout_seconds)

    def _send(self) -> None:
        while (chunk := self._chunks.get()) is not None:
            url = f"{self.annotations_url}/{self.session_dir.name}-{chunk:04d}"
            try:
                httpx.put(url, timeout=self.timeout_seconds).raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("could not request annotation %s: %s", url, exc)
