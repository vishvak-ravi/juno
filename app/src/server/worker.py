"""Annotates chunks: one thread, a priority queue, several model calls in flight.

The queue is durable because it is the store: a chunk is queued by writing its
annotation record as ``pending`` (``annotate.store``), and a record stays
pending until its call ends. Whatever a stopped server had queued or in flight
is therefore still pending on disk, and the next server queues it again.

The worker's thread runs an event loop. ``annotate.max_concurrency`` tasks on
it take chunks off one priority queue, lowest ``Priority`` first and the most
recent chunk first within a priority, so the chunks recorders are filling now
go ahead of everything the server finds for itself in the store.

With ``annotate.backfill`` on, the thread also looks through the store every
``annotate.scan_interval_seconds`` (``find_work``) and queues every chunk
without a finished annotation.

A call that fails because the provider was busy is made again later, up to
``annotate.max_attempts`` times, and every call waits out the delay the busy
provider earned, so a rate limit slows the whole worker instead of being hit
by each task in turn.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import random
import threading
import time
from enum import IntEnum
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

from pydantic import BaseModel

from annotate.label import Annotation, retry_after_seconds, retryable
from annotate.records import (
    count_rows,
    read_aggregations,
    session_dir,
    session_dirs,
    session_id_of,
)
from annotate.request import AnnotationRequest, chunk_request
from annotate.sanitize import sanitize
from annotate.store import (
    AnnotationRecord,
    covers,
    list_records,
    new_record,
    read_record,
    write_record,
)
from juno.config import AnnotateConfig

logger = logging.getLogger(__name__)

# The model call for one sanitized request; a coroutine function is awaited,
# any other callable runs in a thread.
AnnotateRequest = Callable[[AnnotationRequest], Union[Annotation, Awaitable[Annotation]]]


class Priority(IntEnum):
    """Why a chunk is queued; a lower one is annotated first."""

    LIVE = 0       # a recorder just uploaded the chunk's last row
    REQUESTED = 1  # asked for directly with ``AnnotationWorker.request``
    BACKFILL = 2   # the server found it in its store


class Work(BaseModel):
    """A chunk in the store with no finished annotation."""

    session_id: str
    chunk: int
    # Unix seconds of the chunk's last row.
    end_time: float


class QueueStatus(BaseModel):
    # Chunks waiting for a call, by ``Priority`` name.
    queued: dict[str, int]
    # Chunks whose call is in flight.
    running: int
    # Seconds until the next call may start; above 0 after the provider was busy.
    paused_seconds: float


def find_work(devices_dir: Path, config: AnnotateConfig, now: float) -> list[Work]:
    """Every chunk in the store that should be annotated and has not been.

    That is a chunk with no record, a pending record, a failed record with
    attempts left, or a done record made when the chunk held fewer rows. The
    rows after a session's last full chunk count only once the session has gone
    ``config.tail_idle_seconds`` without a new row.
    """
    work = []
    for directory in session_dirs(devices_dir):
        try:
            timestamps = [aggregation.timestamp for aggregation in read_aggregations(directory)]
        except FileNotFoundError:
            continue
        size = config.chunk_size
        for chunk in range(-(-len(timestamps) // size)):
            rows = timestamps[chunk * size:(chunk + 1) * size]
            record = read_record(directory, chunk)
            if covers(record, len(rows)):
                continue
            if record is not None and record.status == "failed" \
                    and record.attempts >= config.max_attempts:
                continue
            if len(rows) < size and now - max(timestamps) < config.tail_idle_seconds:
                continue
            work.append(Work(session_id=session_id_of(directory), chunk=chunk,
                             end_time=max(rows)))
    return work


def build_request(directory: Path, chunk: int, chunk_size: int) -> tuple[AnnotationRequest, int]:
    """The sanitized request for a chunk, and how many rows the chunk holds."""
    aggregations = read_aggregations(directory)
    request = chunk_request(aggregations, chunk, chunk_size)
    if request is None:
        raise ValueError("the chunk has no aggregation with a screenshot")
    return sanitize(request), len(aggregations[chunk * chunk_size:(chunk + 1) * chunk_size])


class AnnotationWorker:

    def __init__(self, devices_dir: Path, config: AnnotateConfig,
                 annotate_request: AnnotateRequest):
        """
        Args:
            devices_dir: where the server stores every device's sessions.
            annotate_request: the model call for one sanitized request.
        """
        self.devices_dir = devices_dir
        self.config = config
        self.annotate_request = annotate_request
        self._lock = threading.Lock()
        self._request_lock = threading.Lock()
        # Chunk -> the priority it is queued at; it stays here until its record
        # is done or failed.
        self._waiting: dict[tuple[str, int], Priority] = {}
        self._running: set[tuple[str, int]] = set()
        self._order = itertools.count()  # breaks ties between queue entries
        self._paused_until = 0.0  # time.monotonic() before which no call starts
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.PriorityQueue] = None
        self._stop: Optional[asyncio.Event] = None

    # -- called from any thread ---------------------------------------------

    def start(self) -> None:
        """Start the thread and queue the work the store holds (all of it with ``annotate.backfill``)."""
        self._ensure_started()
        if self.config.backfill:
            self._loop.call_soon_threadsafe(
                lambda: self._tasks.append(self._loop.create_task(self._scan_forever())))
            return
        # Without backfill, only what a stopped server left pending is taken up again.
        for record in list_records(self.devices_dir):
            if record.status == "pending":
                self.request(record.session_id, record.chunk, Priority.BACKFILL, record.end_time)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Stop the thread. Calls in flight are dropped; their records stay pending."""
        with self._lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            thread.join(timeout=timeout_seconds)

    def is_waiting(self, session_id: str, chunk: int) -> bool:
        """Whether the chunk is queued or being annotated right now."""
        with self._lock:
            return (session_id, chunk) in self._waiting

    def request(self, session_id: str, chunk: int, priority: Priority = Priority.REQUESTED,
                end_time: Optional[float] = None) -> AnnotationRecord:
        """The chunk's annotation, queued at ``priority`` unless it is finished.

        A chunk that is new, failed, was left pending by a server that stopped,
        or has gained rows since it was annotated gets a pending record and is
        queued. A failed record that ``find_work`` brings back (``BACKFILL``)
        keeps its attempts; any other request starts the count again. A chunk
        already queued keeps its record and moves up if ``priority`` is ahead
        of the one it has.

        Args:
            end_time: Unix seconds of the chunk's last row; None means now.
        """
        directory = session_dir(self.devices_dir, session_id)
        size = self.config.chunk_size
        # One request at a time: the record is read, then written.
        with self._request_lock:
            rows = max(0, min(size, count_rows(directory) - chunk * size))
            record = read_record(directory, chunk)
            if covers(record, rows):
                return record
            if record is None or not self.is_waiting(session_id, chunk):
                attempts = (record.attempts if record is not None and record.status == "failed"
                            and priority == Priority.BACKFILL else 0)
                record = new_record(session_id, chunk, self.config.model).model_copy(
                    update={"attempts": attempts})
                write_record(directory, record)
            self._submit(session_id, chunk, priority, end_time)
            return record

    def _submit(self, session_id: str, chunk: int, priority: Priority,
                end_time: Optional[float]) -> None:
        key = (session_id, chunk)
        with self._lock:
            queued_at = self._waiting.get(key)
            if queued_at is not None and queued_at <= priority:
                return
            self._waiting[key] = priority
        self._ensure_started()
        entry = (int(priority), -(time.time() if end_time is None else end_time),
                 next(self._order), key)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, entry)

    def status(self) -> QueueStatus:
        with self._lock:
            queued = {priority.name.lower(): 0 for priority in Priority}
            for key, priority in self._waiting.items():
                if key not in self._running:
                    queued[priority.name.lower()] += 1
            return QueueStatus(queued=queued, running=len(self._running),
                               paused_seconds=max(0.0, self._paused_until - time.monotonic()))

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            ready = threading.Event()
            self._thread = threading.Thread(target=lambda: asyncio.run(self._main(ready)),
                                            daemon=True, name="annotation-worker")
            self._thread.start()
        ready.wait()

    # -- on the worker's thread ----------------------------------------------

    async def _main(self, ready: threading.Event) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.PriorityQueue()
        self._stop = asyncio.Event()
        self._tasks = [self._loop.create_task(self._work())
                       for _ in range(self.config.max_concurrency)]
        ready.set()
        await self._stop.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _scan_forever(self) -> None:
        while True:
            try:
                queued = await asyncio.to_thread(self._queue_found_work)
                if queued:
                    logger.info("queued %d chunks found in the store", queued)
            except Exception:
                logger.exception("looking through the store failed")
            await asyncio.sleep(self.config.scan_interval_seconds)

    def _queue_found_work(self) -> int:
        """Queue what ``find_work`` finds and is not queued already; returns how many."""
        queued = 0
        found = find_work(self.devices_dir, self.config, time.time())
        # Most recent first, so the first chunks handed over are the first wanted.
        for work in sorted(found, key=lambda work: work.end_time, reverse=True):
            if not self.is_waiting(work.session_id, work.chunk):
                self.request(work.session_id, work.chunk, Priority.BACKFILL, work.end_time)
                queued += 1
        return queued

    async def _work(self) -> None:
        while True:
            *_, key = await self._queue.get()
            with self._lock:
                # An entry left behind by a chunk that moved up, or was finished.
                if key not in self._waiting or key in self._running:
                    continue
                self._running.add(key)
            retry_in = None
            try:
                retry_in = await self._annotate(*key)
            except Exception:
                logger.exception("annotation worker failed on %s chunk %d", *key)
            finally:
                with self._lock:
                    self._running.discard(key)
                    priority = self._waiting[key]
                    if retry_in is None:
                        del self._waiting[key]
            if retry_in is not None:
                entry = (int(priority), -time.time(), next(self._order), key)
                self._loop.call_later(retry_in, self._queue.put_nowait, entry)

    async def _call(self, request: AnnotationRequest) -> Annotation:
        if inspect.iscoroutinefunction(self.annotate_request):
            return await self.annotate_request(request)
        return await asyncio.to_thread(self.annotate_request, request)

    async def _annotate(self, session_id: str, chunk: int) -> Optional[float]:
        """Make one call for the chunk and store how it ended.

        Returns the seconds after which to call again, or None if the record
        is now done or failed.
        """
        directory = session_dir(self.devices_dir, session_id)
        record = await asyncio.to_thread(read_record, directory, chunk)
        if record is None or record.status != "pending":
            return None
        while (wait := self._paused_until - time.monotonic()) > 0:
            await asyncio.sleep(wait)

        update: dict = {"attempts": record.attempts + 1}
        retry_in = None
        try:
            request, rows = await asyncio.to_thread(build_request, directory, chunk,
                                                    self.config.chunk_size)
            update.update(frames=request.frames, rows=rows,
                          start_time=request.frames[0].timestamp,
                          end_time=request.frames[-1].timestamp)
            annotation = await asyncio.wait_for(self._call(request), self.config.timeout_seconds)
            update.update(status="done", annotation=annotation, error=None)
        except Exception as exc:  # recorded on the annotation, whatever the model call raised
            update["error"] = f"{type(exc).__name__}: {exc}"
            if retryable(exc) and update["attempts"] < self.config.max_attempts:
                backoff = min(self.config.retry_max_seconds,
                              self.config.retry_base_seconds * 2 ** record.attempts)
                retry_in = max(backoff * random.uniform(0.5, 1.5), retry_after_seconds(exc) or 0.0)
                self._paused_until = max(self._paused_until, time.monotonic() + retry_in)
                logger.warning("annotation %s: attempt %d failed, next in %.0f s: %s",
                               record.id, update["attempts"], retry_in, exc)
            else:
                update["status"] = "failed"
                logger.warning("annotation %s failed: %s", record.id, exc)
        update["updated_at"] = time.time()
        await asyncio.to_thread(write_record, directory, record.model_copy(update=update))
        return retry_in
