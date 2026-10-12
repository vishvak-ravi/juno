"""``/events``: what every device recorded, as one list ordered by time.

An event is one row of a session's ``raw_aggregations.jsonl`` (one screenshot
and the input after it), returned without the recorder's bookkeeping; the row
on disk keeps every field. Event ``<session id>-<row as six digits>`` is row
``row`` (from 0) of that session. The list merges the sessions of every device
and orders them by ``(timestamp, id)``; ``after`` takes the id of the last
event seen and continues past it in that order.

A recorder uploads its session one row at a time, in order, with
``PUT /events/{id}``: a multipart body whose ``aggregation`` part is the row as
the recorder wrote it (JSON) and whose ``screenshot`` part is the PNG that row
points to. The server keeps its own copy of both, and queues the annotation of
each chunk the moment its last row arrives, unless the request says
``?annotate=false``.

``PATCH`` sets a caption of the user's own over the model's; ``DELETE`` hides
the event. Both are kept in the session's ``event_edits.json``: the row, its
screenshot and its annotation stay on disk.
"""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from annotate.records import (
    AGGREGATIONS_FILE,
    SESSION_ID_PATTERN,
    Aggregation,
    count_rows,
    read_aggregations,
    session_dir,
    session_dirs,
    session_id_of,
)
from annotate.store import read_record
from server.routes.paging import DeviceFilter, Page
from server.worker import Priority

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

EVENT_ID = re.compile(rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<row>\d{{6,}})$")
EventId = Annotated[str, PathParam(pattern=EVENT_ID.pattern, max_length=128)]

SCREENSHOTS_DIR = "screenshots"
EDITS_FILE = "event_edits.json"

# An upload counts the session's rows, then appends one; two at once must not
# interleave, so uploads run one at a time. Edits read, change and replace a
# session's edits file, so they too run one at a time.
upload_lock = threading.Lock()
edits_lock = threading.Lock()


class KeyInput(BaseModel):
    type: Literal["key_press", "key_release"]
    timestamp: float
    key: str


class ButtonInput(BaseModel):
    type: Literal["mouse_down", "mouse_up"]
    timestamp: float
    button: str
    # Desktop coordinates; None where the pointer cannot be read (Wayland).
    x: Optional[int]
    y: Optional[int]


class ScrollInput(BaseModel):
    type: Literal["mouse_scroll"]
    timestamp: float
    x: Optional[int]
    y: Optional[int]
    dx: int
    dy: int


class MoveInput(BaseModel):
    type: Literal["mouse_move"]
    timestamp: float
    x: Optional[int]
    y: Optional[int]


Input = Annotated[Union[KeyInput, ButtonInput, ScrollInput, MoveInput],
                  Field(discriminator="type")]


class Event(BaseModel):
    id: str
    device_id: str
    # Unix seconds, by the recording device's clock.
    timestamp: float
    # None if no screenshot was uploaded with the row.
    screenshot_url: Optional[str]
    inputs: list[Input]
    # The user's caption if they set one, else the model's; None until annotated.
    caption: Optional[str]


class EventEdit(BaseModel):
    """What the user changed on one event."""
    caption: Optional[str] = None
    deleted: bool = False


class SessionEdits(BaseModel):
    # By row.
    rows: dict[int, EventEdit] = {}


class EventPatch(BaseModel):
    # null removes the user's caption, showing the model's again.
    caption: Optional[str]


def read_edits(directory: Path) -> SessionEdits:
    try:
        return SessionEdits.model_validate_json((directory / EDITS_FILE).read_text())
    except FileNotFoundError:
        return SessionEdits()


def write_edits(directory: Path, edits: SessionEdits) -> None:
    """Replace the file in one step, so a reader never sees half of it."""
    path = directory / EDITS_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(edits.model_dump_json(indent=2))
    os.replace(temporary, path)


def model_captions(directory: Path, aggregations: list[Aggregation],
                   chunk_size: int) -> dict[int, str]:
    """The model's caption of each row its annotations cover, by row.

    Frame ``f`` of a chunk's annotation is the ``f``-th of the chunk's rows
    that have a screenshot, ordered by screenshot time: the rows
    ``annotate.request.build_request`` sends, in the order napsack's
    ``sanitize_records`` puts them.
    """
    captions = {}
    for start in range(0, len(aggregations), chunk_size):
        try:
            record = read_record(directory, start // chunk_size)
        except ValidationError as exc:
            logger.warning("skipping the annotation of %s chunk %d: %s",
                           session_id_of(directory), start // chunk_size, exc)
            continue
        if record is None or record.annotation is None:
            continue
        framed = sorted((row for row in range(start, min(start + chunk_size, len(aggregations)))
                         if aggregations[row].screenshot_path is not None
                         and aggregations[row].screenshot_timestamp is not None),
                        key=lambda row: aggregations[row].screenshot_timestamp)
        for action in record.annotation.actions:
            for row in framed[action.start - 1:action.end]:
                captions[row] = action.caption
    return captions


def session_events(directory: Path, chunk_size: int,
                   include_deleted: bool = False) -> dict[int, Event]:
    """The events of one session, by row; none if it has no rows."""
    try:
        aggregations = read_aggregations(directory)
    except FileNotFoundError:
        return {}
    session_id = session_id_of(directory)
    device_id = session_id.split("-", 1)[0]
    edits = read_edits(directory).rows
    captions = model_captions(directory, aggregations, chunk_size)
    events = {}
    for row, aggregation in enumerate(aggregations):
        edit = edits.get(row, EventEdit())
        if edit.deleted and not include_deleted:
            continue
        event_id = f"{session_id}-{row:06d}"
        events[row] = Event(
            id=event_id, device_id=device_id, timestamp=aggregation.timestamp,
            screenshot_url=(f"/events/{event_id}/screenshot"
                            if aggregation.screenshot_path else None),
            inputs=[{"type": e.event_type, "timestamp": e.timestamp, **e.details}
                    for e in aggregation.events],
            caption=edit.caption if edit.caption is not None else captions.get(row))
    return events


def locate(devices_dir: Path, event_id: str) -> tuple[Path, int]:
    """The session directory and row of ``event_id``."""
    match = EVENT_ID.match(event_id)
    return session_dir(devices_dir, match.group("session_id")), int(match.group("row"))


def find(request: Request, event_id: str, include_deleted: bool = False) -> Event:
    state = request.app.state
    directory, row = locate(state.devices_dir, event_id)
    event = session_events(directory, state.annotate.chunk_size, include_deleted).get(row)
    if event is None:
        raise HTTPException(status_code=404, detail=f"no event with id {event_id}")
    return event


def sort_key(event: Event) -> tuple[float, str]:
    return event.timestamp, event.id


@router.get("")
def list_events(
    request: Request,
    device: DeviceFilter = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[Event]:
    """Events of every device, newest first by default, optionally of one device or time range."""
    state = request.app.state
    events = [event for directory in session_dirs(state.devices_dir, device)
              for event in session_events(directory, state.annotate.chunk_size).values()
              if (since is None or event.timestamp >= since)
              and (until is None or event.timestamp <= until)]
    events.sort(key=sort_key, reverse=order == "desc")
    if after is not None:
        # By its place in the order, not in this list: the event may have been
        # deleted since, or rows recorded earlier may have arrived before it.
        if not EVENT_ID.match(after):
            raise HTTPException(status_code=400, detail=f"{after} is not an event id")
        try:
            cursor = sort_key(find(request, after, include_deleted=True))
        except HTTPException:
            raise HTTPException(status_code=400, detail=f"nothing with id {after} to page after")
        events = [event for event in events
                  if (sort_key(event) < cursor if order == "desc" else sort_key(event) > cursor)]
    data = events[:limit]
    return Page(data=data,
                first_id=data[0].id if data else None,
                last_id=data[-1].id if data else None,
                has_more=len(events) > limit)


@router.get("/{event_id}")
def get_event(event_id: EventId, request: Request) -> Event:
    return find(request, event_id)


@router.patch("/{event_id}")
def update_event(event_id: EventId, body: EventPatch, request: Request) -> Event:
    """Set the event's caption; the model's stays on disk and returns if this is null."""
    find(request, event_id)
    directory, row = locate(request.app.state.devices_dir, event_id)
    with edits_lock:
        edits = read_edits(directory)
        edits.rows.setdefault(row, EventEdit()).caption = body.caption
        write_edits(directory, edits)
    return find(request, event_id)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: EventId, request: Request) -> None:
    """Hide the event from ``/events``; its row and screenshot stay on disk."""
    find(request, event_id)
    directory, row = locate(request.app.state.devices_dir, event_id)
    with edits_lock:
        edits = read_edits(directory)
        edits.rows.setdefault(row, EventEdit()).deleted = True
        write_edits(directory, edits)


@router.put("/{event_id}")
def upload_event(event_id: EventId, request: Request, response: Response,
                 aggregation: Annotated[str, Form()],
                 screenshot: Optional[UploadFile] = None,
                 annotate: bool = True) -> Event:
    """Store the next row of a session, and its screenshot; safe to repeat.

    201 when the row is stored. 200, storing nothing, if the session already
    has that row. 409 if rows before it are missing; ``expected_row`` in the
    detail is the one to send next. With ``annotate`` false the row is only
    stored; with ``annotate.backfill`` the server annotates its chunk later.
    """
    state = request.app.state
    directory, row = locate(state.devices_dir, event_id)
    session_id = session_id_of(directory)
    try:
        record = json.loads(aggregation)
        Aggregation.model_validate(record)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"not an aggregation row: {exc}")

    with upload_lock:
        stored = count_rows(directory)
        if row < stored:
            return find(request, event_id, include_deleted=True)
        if row > stored:
            raise HTTPException(status_code=409, detail={
                "message": f"session {session_id} has {stored} rows; send row {stored} next",
                "expected_row": stored})

        # The row arrives pointing at the recorder's file; it is stored pointing
        # at this server's copy, or at nothing if no screenshot came with it.
        path = None
        if screenshot is not None and record["screenshot_path"]:
            # PurePath.name of either platform's path: no directory part survives.
            name = re.split(r"[\\/]", record["screenshot_path"])[-1]
            path = directory / SCREENSHOTS_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(screenshot.file.read())
        record["screenshot_path"] = str(path) if path else None
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / AGGREGATIONS_FILE, "a") as rows:
            rows.write(json.dumps(record) + "\n")

    chunk_size = state.annotate.chunk_size
    if annotate and (row + 1) % chunk_size == 0:
        state.worker.request(session_id, row // chunk_size, Priority.LIVE)
    response.status_code = 201
    return find(request, event_id, include_deleted=True)


@router.get("/{event_id}/screenshot")
def get_screenshot(event_id: EventId, request: Request) -> FileResponse:
    find(request, event_id)
    directory, row = locate(request.app.state.devices_dir, event_id)
    aggregation = read_aggregations(directory)[row]
    path = Path(aggregation.screenshot_path).resolve() if aggregation.screenshot_path else None
    # Only files stored inside that session are served.
    if path is None or not path.is_file() or directory.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail=f"{event_id} has no screenshot")
    return FileResponse(path, media_type="image/png")
