"""Activity captions: the model's account of what the user did in each chunk of a session.

Chunk ``n`` of a session is rows ``n * chunk_size`` up to ``(n + 1) * chunk_size``
of its events. Its caption has the id ``<session id>-<n as four digits>``
(session ids as in ``juno.sessions``). The record holds what was sent (frames,
after sanitizing), how it was sanitized, the model, and what came back, so two
models or two sanitizers can be compared on the same chunk later.

``ActivityCaptions`` is what the server reads and writes through;
``FileActivityCaptions`` keeps chunk ``n`` of a session in
``<session dir>/activity_captions/<n as four digits>.json``.
``read_caption_file`` and ``write_caption_file`` read and write that layout
under any directory, for scripts that keep their own runs. A frame's screenshot
path is stored relative to that directory when the screenshot is inside it.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ValidationError

from annotate.label import Annotation
from annotate.request import AnnotationFrame
from juno.sessions import (
    SESSION_ID_PATTERN,
    replace_file,
    resolved,
    session_dir,
    session_dirs,
    stored_path,
)

logger = logging.getLogger(__name__)

CAPTIONS_DIR = "activity_captions"
CAPTION_ID = re.compile(rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<chunk>\d{{4,}})$")

Status = Literal["pending", "done", "failed"]


class ActivityCaption(BaseModel):
    id: str
    # The device whose activity this describes; the start of ``session_id``.
    device_id: str
    session_id: str
    chunk: int
    # pending: waiting for the model. failed: ``error`` says why; a new PUT retries.
    status: Status
    # Unix seconds.
    created_at: float
    updated_at: float
    model: str
    # The frames as sent, sanitized; empty until the request is built.
    frames: list[AnnotationFrame] = []
    # Screenshot times of the first and last frame; None until the request is built.
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    # The model's output with the sanitization of its request; None unless done.
    annotation: Optional[Annotation] = None
    error: Optional[str] = None
    # Model calls made for this record so far, failed ones included.
    attempts: int = 0
    # Rows the chunk held when it was captioned; None on records without this field.
    rows: Optional[int] = None


class ActivityCaptions(Protocol):

    def get(self, session_id: str, chunk: int) -> Optional[ActivityCaption]:
        """The chunk's caption, or None if there is none."""

    def save(self, caption: ActivityCaption) -> None:
        """Store the caption, replacing the chunk's earlier one."""

    def all(self, session_id: Optional[str] = None, device_id: Optional[str] = None,
            since: Optional[float] = None, until: Optional[float] = None) -> list[ActivityCaption]:
        """Stored captions, ordered by session and chunk.

        ``session_id`` and ``device_id`` keep the captions of that session or
        device. ``since`` and ``until`` (Unix seconds) keep the captions whose
        frames overlap that range; a caption with no frames yet has no time and
        is dropped when either is given.
        """


def caption_id(session_id: str, chunk: int) -> str:
    return f"{session_id}-{chunk:04d}"


def parse_caption_id(value: str) -> Optional[tuple[str, int]]:
    """``(session id, chunk)`` for a caption id, or None if it is not one."""
    match = CAPTION_ID.match(value)
    return (match.group("session_id"), int(match.group("chunk"))) if match else None


def caption_file(directory: Path, chunk: int) -> Path:
    return directory / CAPTIONS_DIR / f"{chunk:04d}.json"


def new_caption(session_id: str, chunk: int, model: str) -> ActivityCaption:
    now = time.time()
    return ActivityCaption(id=caption_id(session_id, chunk),
                           device_id=session_id.split("-", 1)[0], session_id=session_id,
                           chunk=chunk, status="pending", created_at=now, updated_at=now,
                           model=model)


def covers(caption: Optional[ActivityCaption], rows: int) -> bool:
    """Whether ``caption`` is finished and was made of a chunk holding ``rows`` rows or more.

    A done caption made when the chunk held fewer rows (a session's last, still
    growing chunk) does not cover it.
    """
    return (caption is not None and caption.status == "done"
            and (caption.rows is None or caption.rows >= rows))


def parse_caption(directory: Path, text: str) -> ActivityCaption:
    caption = ActivityCaption.model_validate_json(text)
    for frame in caption.frames:
        frame.screenshot_path = resolved(directory, frame.screenshot_path)
    return caption


def read_caption_file(directory: Path, chunk: int) -> Optional[ActivityCaption]:
    """The caption of a chunk stored under ``directory``, or None if there is none."""
    try:
        return parse_caption(directory, caption_file(directory, chunk).read_text())
    except FileNotFoundError:
        return None


def write_caption_file(directory: Path, caption: ActivityCaption) -> None:
    frames = [frame.model_copy(update={"screenshot_path": stored_path(directory, frame.screenshot_path)})
              for frame in caption.frames]
    replace_file(caption_file(directory, caption.chunk),
                 caption.model_copy(update={"frames": frames}).model_dump_json(indent=2))


class FileActivityCaptions:

    def __init__(self, devices_dir: Path):
        self.devices_dir = devices_dir

    def get(self, session_id: str, chunk: int) -> Optional[ActivityCaption]:
        return read_caption_file(session_dir(self.devices_dir, session_id), chunk)

    def save(self, caption: ActivityCaption) -> None:
        write_caption_file(session_dir(self.devices_dir, caption.session_id), caption)

    def all(self, session_id: Optional[str] = None, device_id: Optional[str] = None,
            since: Optional[float] = None, until: Optional[float] = None) -> list[ActivityCaption]:
        captions = []
        for directory in session_dirs(self.devices_dir, device_id):
            for path in (directory / CAPTIONS_DIR).glob("*.json"):
                try:
                    caption = parse_caption(directory, path.read_text())
                except (OSError, ValidationError) as exc:
                    logger.warning("skipping %s: %s", path, exc)
                    continue
                if session_id is not None and caption.session_id != session_id:
                    continue
                if since is not None and (caption.end_time is None or caption.end_time < since):
                    continue
                if until is not None and (caption.start_time is None or caption.start_time > until):
                    continue
                captions.append(caption)
        return sorted(captions, key=lambda c: (c.session_id, c.chunk))
