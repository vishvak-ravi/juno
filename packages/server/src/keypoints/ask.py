"""Asks Jev about every row of each captioned chunk and stores the answers.

Each row is one request. Its state is the row's screenshot with the pointer
drawn in, its input text and its caption, both as the activity caption was
sent and returned. A row with no input (``questions.active``) is asked the
``screen`` questions only.

The worker looks through the store every ``annotate.scan_interval_seconds``
for chunks whose activity caption is done and has no answers, or is newer than
the one its answers were made from, and asks about the most recent first. Clearing
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
from typing import Any, Awaitable, Callable, Optional

from PIL import Image as PilImage
from pydantic import BaseModel
from typesafe_sdk import AsyncTypeSafeClient, Image, TypeSafeAPIError, TypeSafeError

from annotate.request import AnnotationFrame, frame_image, framed_rows
from annotate.store import ActivityCaption, ActivityCaptions
from juno.config import AnnotateConfig, KeypointsConfig
from juno.sessions import Aggregation
from keypoints.questions import QuestionSet, active
from keypoints.store import ChunkAnswers, QuestionAnswers, RowAnswers
from server.events import Events

logger = logging.getLogger(__name__)

IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
# TypeSafe's limits on each side of an image, in pixels.
MIN_SIDE = 280
MAX_SIDE = 1568
# HTTP statuses of a request TypeSafe will refuse however often it is sent.
REFUSED = frozenset({400, 413, 422})

# One row's request: its state and questions, answered.
Ask = Callable[[dict[str, Any], dict[str, dict[str, Any]]], Awaitable[RowAnswers]]


class JevAsk:
    """The TypeSafe call; the client is made on the first call, on the caller's event loop."""

    def __init__(self, model: str):
        self.model = model
        self.client: Optional[AsyncTypeSafeClient] = None

    async def __call__(self, state: dict[str, Any],
                       questions: dict[str, dict[str, Any]]) -> RowAnswers:
        if self.client is None:
            self.client = AsyncTypeSafeClient(model=self.model)
        result = await self.client.system_one(state, questions,
                                              extra_headers={IMAGES_HEADER: "true"})
        return RowAnswers.model_validate({
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


def frame_captions(record: ActivityCaption) -> dict[int, str]:
    """The caption of each frame of the activity caption, by frame (from 0)."""
    captions = {}
    for action in record.annotation.actions if record.annotation else []:
        for frame in range(action.start - 1, action.end):
            captions[frame] = action.caption
    return captions


def row_state(frame: AnnotationFrame, caption: Optional[str]) -> dict[str, Any]:
    return {"screenshot": Image.from_pil(fitted(frame_image(frame)), "jpeg"),
            "inputs": frame.events, "caption": caption}


def needs_answers(record: ActivityCaption, answers: Optional[ChunkAnswers]) -> bool:
    return (record.status == "done" and record.annotation is not None and bool(record.frames)
            and (answers is None or answers.caption_updated_at < record.updated_at))


def find_work(captions: ActivityCaptions, answers: QuestionAnswers) -> list[Work]:
    """Every captioned chunk without answers to its caption, most recent first."""
    work = [Work(session_id=record.session_id, chunk=record.chunk, end_time=record.end_time or 0.0)
            for record in captions.all()
            if needs_answers(record, answers.get(record.session_id, record.chunk))]
    return sorted(work, key=lambda w: w.end_time, reverse=True)


async def ask_chunk(aggregations: list[Aggregation], record: ActivityCaption, chunk_size: int,
                    questions: QuestionSet, ask: Ask, model: str,
                    limit: asyncio.Semaphore) -> ChunkAnswers:
    """Ask about every framed row of the chunk; raises if a row could not be asked."""
    captions = frame_captions(record)
    # Rows added to the chunk after it was annotated come last by screenshot time.
    pairs = list(zip(framed_rows(aggregations, record.chunk, chunk_size), record.frames))

    async def one(index: int, row: int, frame: AnnotationFrame) -> RowAnswers:
        asked = {**questions.screen, **(questions.action if active(aggregations[row]) else {})}
        async with limit:
            state = await asyncio.to_thread(row_state, frame, captions.get(index))
            try:
                return await ask(state, asked)
            except Exception as exc:
                if not refused(exc):
                    raise
                logger.warning("jev refused %s row %d: %s", record.session_id, row, exc)
                return RowAnswers(error=f"{type(exc).__name__}: {exc}")

    answered = await asyncio.gather(*(one(index, row, frame)
                                      for index, (row, frame) in enumerate(pairs)))
    return ChunkAnswers(session_id=record.session_id, chunk=record.chunk, model=model,
                        questions=questions.version,
                        caption_updated_at=record.updated_at, created_at=time.time(),
                        rows={row: answers for (row, _), answers in zip(pairs, answered)})


class QuestionWorker:

    def __init__(self, events: Events, captions: ActivityCaptions, answers: QuestionAnswers,
                 annotate: AnnotateConfig, config: KeypointsConfig, questions: QuestionSet,
                 ask: Ask, asking: bool = True):
        """
        Args:
            ask: the model call for one row.
            asking: whether calls are made; False starts the worker paused.
        """
        self.events = events
        self.captions = captions
        self.answers = answers
        self.annotate = annotate
        self.config = config
        self.questions = questions
        self.ask = ask
        self._asking = threading.Event()
        if asking:
            self._asking.set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()),
                                        daemon=True, name="question-worker")
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Stop after the chunk being asked about; a chunk cut short is asked again on the next start."""
        self._stop.set()
        self._asking.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)

    def set_asking(self, on: bool) -> None:
        """Resume (True) or pause (False) before the next chunk."""
        if on:
            self._asking.set()
        else:
            self._asking.clear()

    async def _main(self) -> None:
        limit = asyncio.Semaphore(self.config.max_concurrency)
        while not self._stop.is_set():
            await asyncio.to_thread(self._asking.wait)
            try:
                found = await asyncio.to_thread(find_work, self.captions, self.answers)
            except Exception:
                logger.exception("looking through the store for chunks to ask about failed")
                found = []
            for work in found:
                if self._stop.is_set() or not self._asking.is_set():
                    break
                await self._ask(work, limit)
            await asyncio.to_thread(self._stop.wait, self.annotate.scan_interval_seconds)

    async def _ask(self, work: Work, limit: asyncio.Semaphore) -> None:
        try:
            record = await asyncio.to_thread(self.captions.get, work.session_id, work.chunk)
            aggregations = await asyncio.to_thread(self.events.rows, work.session_id)
            answers = await ask_chunk(aggregations, record, self.annotate.chunk_size,
                                      self.questions, self.ask, self.config.model, limit)
            await asyncio.to_thread(self.answers.save, answers)
        except Exception as exc:  # the chunk is asked again on a later look
            logger.warning("asking about %s chunk %d failed: %s", work.session_id, work.chunk, exc)
