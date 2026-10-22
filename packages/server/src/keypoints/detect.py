"""Asks Jev about every row of each annotated chunk and stores the answers.

Each row is one request. Its state is the row's screenshot with the pointer
drawn in, its input text and its caption, both as the annotation was sent and
returned. A row with no input (``questions.active``) is asked the ``screen``
questions only.

The worker looks through the store every ``annotate.scan_interval_seconds``
for chunks whose annotation is done and has no answers, or newer ones than
its answers were made from, and asks about the most recent first. Clearing
the server's ``annotate`` switch pauses it before its next chunk. A chunk
whose rows could not all be asked, because TypeSafe was busy or could not be
reached, is not stored and is asked again on a later look; a row TypeSafe
refuses is stored with its error.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from PIL import Image as PilImage
from pydantic import BaseModel
from typesafe_sdk import AsyncTypeSafeClient, Image, TypeSafeAPIError, TypeSafeError

from annotate.request import AnnotationFrame, frame_image, framed_rows
from annotate.store import AnnotationRecord, list_records, read_record
from juno.config import AnnotateConfig, KeypointsConfig
from juno.sessions import read_aggregations, session_dir
from keypoints.questions import QuestionSet, active
from keypoints.store import DetectionRecord, RowDetection, read_detection, write_detection

logger = logging.getLogger(__name__)

IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
# TypeSafe's limits on each side of an image, in pixels.
MIN_SIDE = 280
MAX_SIDE = 1568
# HTTP statuses of a request TypeSafe will refuse however often it is sent.
REFUSED = frozenset({400, 413, 422})

# One row's request: its state and questions, answered.
Ask = Callable[[dict[str, Any], dict[str, dict[str, Any]]], Awaitable[RowDetection]]


class JevAsk:
    """The TypeSafe call; the client is made on the first call, on the caller's event loop."""

    def __init__(self, model: str):
        self.model = model
        self.client: Optional[AsyncTypeSafeClient] = None

    async def __call__(self, state: dict[str, Any],
                       questions: dict[str, dict[str, Any]]) -> RowDetection:
        if self.client is None:
            self.client = AsyncTypeSafeClient(model=self.model)
        result = await self.client.system_one(state, questions,
                                              extra_headers={IMAGES_HEADER: "true"})
        return RowDetection.model_validate({
            "answers": {key: answer.model_dump(mode="json") for key, answer in result.answers.items()},
            "input_tokens": result.usage.input_tokens})


class Work(BaseModel):
    session_id: str
    chunk: int
    # Unix seconds of the chunk's last frame.
    end_time: float


def refused(exc: Exception) -> bool:
    """Whether asking again would get the same error: a refused request or an image the SDK rejects."""
    if isinstance(exc, TypeSafeAPIError):
        return exc.status in REFUSED
    return type(exc) is TypeSafeError


def fitted(image: PilImage.Image) -> PilImage.Image:
    """``image`` scaled to TypeSafe's limits, keeping its aspect ratio."""
    image = image.copy()
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    short = min(image.size)
    if short < MIN_SIDE:
        scale = MIN_SIDE / short
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    return image


def frame_captions(record: AnnotationRecord) -> dict[int, str]:
    """The caption of each frame of the annotation, by frame (from 0)."""
    captions = {}
    for action in record.annotation.actions if record.annotation else []:
        for frame in range(action.start - 1, action.end):
            captions[frame] = action.caption
    return captions


def row_state(frame: AnnotationFrame, caption: Optional[str]) -> dict[str, Any]:
    return {"screenshot": Image.from_pil(fitted(frame_image(frame)), "jpeg"),
            "inputs": frame.events, "caption": caption}


def needs_detection(record: AnnotationRecord, detection: Optional[DetectionRecord]) -> bool:
    return (record.status == "done" and record.annotation is not None and bool(record.frames)
            and (detection is None or detection.annotation_updated_at < record.updated_at))


def find_work(devices_dir: Path) -> list[Work]:
    """Every annotated chunk without answers to its annotation, most recent first."""
    work = [Work(session_id=record.session_id, chunk=record.chunk, end_time=record.end_time or 0.0)
            for record in list_records(devices_dir)
            if needs_detection(record, read_detection(session_dir(devices_dir, record.session_id),
                                                      record.chunk))]
    return sorted(work, key=lambda w: w.end_time, reverse=True)


async def detect_chunk(directory: Path, record: AnnotationRecord, chunk_size: int,
                       questions: QuestionSet, ask: Ask, model: str,
                       limit: asyncio.Semaphore) -> DetectionRecord:
    """Ask about every framed row of the chunk; raises if a row could not be asked."""
    aggregations = await asyncio.to_thread(read_aggregations, directory)
    captions = frame_captions(record)
    # Rows added to the chunk after it was annotated come last by screenshot time.
    pairs = list(zip(framed_rows(aggregations, record.chunk, chunk_size), record.frames))

    async def one(index: int, row: int, frame: AnnotationFrame) -> RowDetection:
        asked = {**questions.screen, **(questions.action if active(aggregations[row]) else {})}
        async with limit:
            state = await asyncio.to_thread(row_state, frame, captions.get(index))
            try:
                return await ask(state, asked)
            except Exception as exc:
                if not refused(exc):
                    raise
                logger.warning("jev refused %s row %d: %s", record.session_id, row, exc)
                return RowDetection(error=f"{type(exc).__name__}: {exc}")

    detections = await asyncio.gather(*(one(index, row, frame)
                                        for index, (row, frame) in enumerate(pairs)))
    return DetectionRecord(session_id=record.session_id, chunk=record.chunk, model=model,
                           questions=questions.version,
                           annotation_updated_at=record.updated_at, created_at=time.time(),
                           rows={row: detection for (row, _), detection in zip(pairs, detections)})


class DetectionWorker:

    def __init__(self, devices_dir: Path, annotate: AnnotateConfig, config: KeypointsConfig,
                 questions: QuestionSet, ask: Ask, detecting: bool = True):
        """
        Args:
            ask: the model call for one row.
            detecting: whether calls are made; False starts the worker paused.
        """
        self.devices_dir = devices_dir
        self.annotate = annotate
        self.config = config
        self.questions = questions
        self.ask = ask
        self._detecting = threading.Event()
        if detecting:
            self._detecting.set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()),
                                        daemon=True, name="detection-worker")
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Stop after the chunk being asked about; a chunk cut short is asked again on the next start."""
        self._stop.set()
        self._detecting.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)

    def set_detecting(self, on: bool) -> None:
        """Resume (True) or pause (False) before the next chunk."""
        if on:
            self._detecting.set()
        else:
            self._detecting.clear()

    async def _main(self) -> None:
        limit = asyncio.Semaphore(self.config.max_concurrency)
        while not self._stop.is_set():
            await asyncio.to_thread(self._detecting.wait)
            try:
                found = await asyncio.to_thread(find_work, self.devices_dir)
            except Exception:
                logger.exception("looking through the store for chunks to detect failed")
                found = []
            for work in found:
                if self._stop.is_set() or not self._detecting.is_set():
                    break
                await self._detect(work, limit)
            await asyncio.to_thread(self._stop.wait, self.annotate.scan_interval_seconds)

    async def _detect(self, work: Work, limit: asyncio.Semaphore) -> None:
        directory = session_dir(self.devices_dir, work.session_id)
        try:
            record = await asyncio.to_thread(read_record, directory, work.chunk)
            detection = await detect_chunk(directory, record, self.annotate.chunk_size,
                                           self.questions, self.ask, self.config.model, limit)
            await asyncio.to_thread(write_detection, directory, detection)
        except Exception as exc:  # the chunk is asked again on a later look
            logger.warning("detecting %s chunk %d failed: %s", work.session_id, work.chunk, exc)
