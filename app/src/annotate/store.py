"""Annotations on disk: one JSON file per chunk of a session.

Chunk ``n`` of a session is rows ``n * chunk_size`` up to ``(n + 1) * chunk_size``
of its ``raw_aggregations.jsonl``. Its annotation has the id
``<session id>-<n as four digits>`` (session ids as in ``annotate.records``) and
is stored in ``<session dir>/annotations/<n as four digits>.json``. The file holds what was
sent (frames, after sanitizing), how it was sanitized, the model, and what came
back, so two models or two sanitizers can be compared on the same chunk later.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from annotate.label import Annotation
from annotate.records import SESSION_ID_PATTERN, session_dirs
from annotate.request import AnnotationFrame

logger = logging.getLogger(__name__)

ANNOTATIONS_DIR = "annotations"
ANNOTATION_ID = re.compile(rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<chunk>\d{{4,}})$")

Status = Literal["pending", "done", "failed"]


class AnnotationRecord(BaseModel):
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
    # Rows the chunk held when it was annotated; None on records older than this field.
    rows: Optional[int] = None


def annotation_id(session_id: str, chunk: int) -> str:
    return f"{session_id}-{chunk:04d}"


def parse_annotation_id(value: str) -> Optional[tuple[str, int]]:
    """``(session id, chunk)`` for an annotation id, or None if it is not one."""
    match = ANNOTATION_ID.match(value)
    return (match.group("session_id"), int(match.group("chunk"))) if match else None


def annotation_file(session_dir: Path, chunk: int) -> Path:
    return session_dir / ANNOTATIONS_DIR / f"{chunk:04d}.json"


def new_record(session_id: str, chunk: int, model: str) -> AnnotationRecord:
    now = time.time()
    return AnnotationRecord(id=annotation_id(session_id, chunk),
                            device_id=session_id.split("-", 1)[0], session_id=session_id,
                            chunk=chunk, status="pending", created_at=now, updated_at=now,
                            model=model)


def covers(record: Optional[AnnotationRecord], rows: int) -> bool:
    """Whether ``record`` is a finished annotation of a chunk that now holds ``rows`` rows.

    A done record made when the chunk held fewer rows (a session's last, still
    growing chunk) does not cover it.
    """
    return (record is not None and record.status == "done"
            and (record.rows is None or record.rows >= rows))


def read_record(session_dir: Path, chunk: int) -> Optional[AnnotationRecord]:
    """The stored annotation of a chunk, or None if there is none."""
    try:
        return AnnotationRecord.model_validate_json(annotation_file(session_dir, chunk).read_text())
    except FileNotFoundError:
        return None


def write_record(session_dir: Path, record: AnnotationRecord) -> None:
    """Replace the file in one step, so a reader never sees half a record."""
    path = annotation_file(session_dir, record.chunk)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Its own name per writer: two threads may write the same record at once.
    temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
    temporary.write_text(record.model_dump_json(indent=2))
    os.replace(temporary, path)


def list_records(devices_dir: Path, session_id: Optional[str] = None,
                 since: Optional[float] = None, until: Optional[float] = None,
                 device_id: Optional[str] = None) -> list[AnnotationRecord]:
    """Stored annotations, oldest first within each session; unreadable files are skipped.

    ``session_id`` and ``device_id`` keep the annotations of that session or
    device. ``since`` and ``until`` (Unix seconds) keep the annotations whose
    frames overlap that range. An annotation with no frames yet has no time
    and is dropped when either is given.
    """
    records = []
    for path in (path for directory in session_dirs(devices_dir, device_id)
                 for path in (directory / ANNOTATIONS_DIR).glob("*.json")):
        try:
            record = AnnotationRecord.model_validate_json(path.read_text())
        except (OSError, ValidationError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        if session_id is not None and record.session_id != session_id:
            continue
        if since is not None and (record.end_time is None or record.end_time < since):
            continue
        if until is not None and (record.start_time is None or record.start_time > until):
            continue
        records.append(record)
    return sorted(records, key=lambda r: (r.session_id, r.chunk))
