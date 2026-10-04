"""Runs pending annotations, one at a time, on a background thread.

One at a time keeps the requests to the model's provider in order and under
its per-minute limit. The thread starts with the first submission. The worker
dies with the server; what it had not finished stays pending on disk and is
queued again by ``resume_pending`` when the next server starts.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from annotate.label import Annotation
from annotate.records import read_aggregations
from annotate.request import AnnotationRequest, chunk_request
from annotate.sanitize import sanitize
from annotate.store import AnnotationRecord, list_records, read_record, write_record
from juno.config import AnnotateConfig

logger = logging.getLogger(__name__)


def annotated(record: AnnotationRecord, session_dir: Path, chunk_size: int,
              annotate_request: Callable[[AnnotationRequest], Annotation]) -> AnnotationRecord:
    """``record`` after its one model call: done with the output, or failed with the reason."""
    update: dict = {}
    try:
        request = chunk_request(read_aggregations(session_dir), record.chunk, chunk_size)
        if request is None:
            raise ValueError("the chunk has no aggregation with a screenshot")
        request = sanitize(request)
        update = {"frames": request.frames,
                  "start_time": request.frames[0].timestamp,
                  "end_time": request.frames[-1].timestamp}
        update.update(status="done", annotation=annotate_request(request), error=None)
    except Exception as exc:  # recorded on the annotation, whatever the model call raised
        logger.warning("annotation %s failed: %s", record.id, exc)
        update.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return record.model_copy(update={**update, "updated_at": time.time()})


class AnnotationWorker:

    def __init__(self, sessions_dir: Path, config: AnnotateConfig,
                 annotate_request: Callable[[AnnotationRequest], Annotation]):
        """
        Args:
            sessions_dir: where the session directories are.
            annotate_request: the model call for one sanitized request.
        """
        self.sessions_dir = sessions_dir
        self.config = config
        self.annotate_request = annotate_request
        self._queue: queue.Queue[tuple[str, int]] = queue.Queue()
        self._waiting: set[tuple[str, int]] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def is_waiting(self, session_id: str, chunk: int) -> bool:
        """Whether the chunk is queued or being annotated right now."""
        with self._lock:
            return (session_id, chunk) in self._waiting

    def submit(self, session_id: str, chunk: int) -> None:
        """Queue the chunk; its pending record must already be on disk."""
        with self._lock:
            if (session_id, chunk) in self._waiting:
                return
            self._waiting.add((session_id, chunk))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="annotation-worker")
                self._thread.start()
        self._queue.put((session_id, chunk))

    def resume_pending(self) -> int:
        """Queue every annotation a previous server left pending; returns how many."""
        pending = [record for record in list_records(self.sessions_dir)
                   if record.status == "pending"]
        for record in pending:
            self.submit(record.session_id, record.chunk)
        return len(pending)

    def _run(self) -> None:
        while True:
            session_id, chunk = self._queue.get()
            try:
                session_dir = self.sessions_dir / session_id
                record = read_record(session_dir, chunk)
                if record is not None and record.status == "pending":
                    write_record(session_dir, annotated(
                        record, session_dir, self.config.chunk_size, self.annotate_request))
            except Exception:
                logger.exception("annotation worker failed on %s chunk %d", session_id, chunk)
            finally:
                with self._lock:
                    self._waiting.discard((session_id, chunk))
