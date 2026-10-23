"""What the server's store holds, session by session, and what is wrong with it.

``check_store`` reads every session under the devices directory, in the
layout ``server.events.FileEvents`` and ``annotate.store.FileActivityCaptions``
write, and returns a ``StoreReport``: per session, how much of it arrived (rows
and screenshots) and how much of it is captioned, plus an ``Issue`` for each
thing on disk that breaks a rule below. It runs when the server starts and
behind ``GET /api/integrity``.

The rules, each an ``IssueKind``:

* ``row_unreadable``: a line of ``event_rows.jsonl`` is not an ``Aggregation``.
* ``screenshot_missing``: a row names a screenshot file that does not exist.
* ``screenshot_outside_session``: a row's screenshot is not inside its session directory.
* ``caption_unreadable``: an activity caption file is not an ``ActivityCaption``.
* ``caption_misfiled``: a caption's id, device or session disagree with the
  file it is stored in.
* ``caption_without_rows``: a caption is for a chunk the session has no row of.
* ``caption_incomplete``: a caption is done but has no output or no frames.

A chunk that is not captioned yet, or whose caption failed, is a status
(counted on the session), not an issue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from annotate.store import CAPTIONS_DIR, ActivityCaption, caption_id
from juno.sessions import Aggregation, resolved, session_dirs, session_id_of
from server.events import EVENT_ROWS_FILE

IssueKind = Literal[
    "row_unreadable", "screenshot_missing", "screenshot_outside_session",
    "caption_unreadable", "caption_misfiled", "caption_without_rows",
    "caption_incomplete",
]


class Issue(BaseModel):
    kind: IssueKind
    session_id: str
    # The row (from 0) or chunk the issue is about, or the file's name.
    subject: str
    detail: str


class SessionReport(BaseModel):
    id: str
    device_id: str
    # Unix seconds of the first and last row; None for a session with no row.
    start_time: Optional[float]
    end_time: Optional[float]

    # What arrived.
    rows: int
    # Rows that name a screenshot, and how many of those files are stored.
    screenshots_expected: int
    screenshots_stored: int

    # What is captioned. A chunk is ``chunk_size`` rows; the rows left over at
    # the end of the session (``rows_after_last_chunk``) are never captioned.
    chunks: int
    rows_after_last_chunk: int
    done: int
    pending: int
    failed: int
    # Full chunks nobody has asked to caption.
    not_requested: int


class DeviceReport(BaseModel):
    id: str
    sessions: int
    rows: int
    chunks: int
    done: int
    pending: int
    failed: int
    not_requested: int
    issues: int


class StoreReport(BaseModel):
    devices: list[DeviceReport]
    sessions: list[SessionReport]
    issues: list[Issue]


def check_session(directory: Path, chunk_size: int) -> tuple[SessionReport, list[Issue]]:
    session_id = session_id_of(directory)
    device_id = session_id.split("-", 1)[0]
    issues: list[Issue] = []

    def issue(kind: IssueKind, subject: object, detail: str) -> None:
        issues.append(Issue(kind=kind, session_id=session_id, subject=str(subject), detail=detail))

    try:
        lines = [line for line in (directory / EVENT_ROWS_FILE).read_text().splitlines()
                 if line.strip()]
    except FileNotFoundError:
        lines = []
    timestamps = []
    expected = stored = 0
    for row, line in enumerate(lines):
        try:
            aggregation = Aggregation.model_validate_json(line)
        except ValidationError as exc:
            issue("row_unreadable", row, str(exc).splitlines()[0])
            continue
        timestamps.append(aggregation.timestamp)
        if aggregation.screenshot_path is None:
            continue
        expected += 1
        path = Path(resolved(directory, aggregation.screenshot_path))
        if directory.resolve() not in path.resolve().parents:
            issue("screenshot_outside_session", row, aggregation.screenshot_path)
        elif not path.is_file():
            issue("screenshot_missing", row, aggregation.screenshot_path)
        else:
            stored += 1

    chunks = len(lines) // chunk_size
    statuses: dict[int, str] = {}
    for path in sorted((directory / CAPTIONS_DIR).glob("*.json")):
        try:
            record = ActivityCaption.model_validate_json(path.read_text())
        except (OSError, ValidationError) as exc:
            issue("caption_unreadable", path.name, str(exc).splitlines()[0])
            continue
        if (record.id != caption_id(session_id, record.chunk) or record.device_id != device_id
                or record.session_id != session_id or path.stem != f"{record.chunk:04d}"):
            issue("caption_misfiled", path.name,
                  json.dumps({"id": record.id, "device_id": record.device_id,
                              "session_id": record.session_id, "chunk": record.chunk}))
            continue
        if record.chunk * chunk_size >= len(lines):
            issue("caption_without_rows", record.chunk,
                  f"the session has {len(lines)} rows")
        if record.status == "done" and (record.annotation is None or not record.frames):
            issue("caption_incomplete", record.chunk, "done without output or frames")
        statuses[record.chunk] = record.status

    counted = [status for chunk, status in statuses.items() if chunk < chunks]
    report = SessionReport(
        id=session_id, device_id=device_id,
        start_time=min(timestamps, default=None), end_time=max(timestamps, default=None),
        rows=len(lines), screenshots_expected=expected, screenshots_stored=stored,
        chunks=chunks, rows_after_last_chunk=len(lines) - chunks * chunk_size,
        done=counted.count("done"), pending=counted.count("pending"),
        failed=counted.count("failed"), not_requested=chunks - len(counted))
    return report, issues


def check_store(devices_dir: Path, chunk_size: int) -> StoreReport:
    """The state of every session stored under ``devices_dir``."""
    sessions: list[SessionReport] = []
    issues: list[Issue] = []
    for directory in session_dirs(devices_dir):
        report, found = check_session(directory, chunk_size)
        sessions.append(report)
        issues.extend(found)
    devices = []
    for device_id in sorted({session.device_id for session in sessions}):
        own = [session for session in sessions if session.device_id == device_id]
        devices.append(DeviceReport(
            id=device_id, sessions=len(own),
            **{field: sum(getattr(session, field) for session in own)
               for field in ("rows", "chunks", "done", "pending", "failed", "not_requested")},
            issues=sum(1 for found in issues if found.session_id.split("-", 1)[0] == device_id)))
    return StoreReport(devices=devices, sessions=sessions, issues=issues)
