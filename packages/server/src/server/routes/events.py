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
the event. Both are kept as the session's edits (``server.events``): the row,
its screenshot and its activity caption stay stored.

``GET /events/activity?edges=t0,t1,...`` counts the events in each span between
consecutive edges (unix seconds, ascending), for a calendar or any other
overview of when things were recorded.
"""

import bisect
import json
import logging
from typing import Annotated, Literal, Optional, Union

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import State

from juno.events import EVENT_ID, event_id_of
from juno.sessions import Aggregation
from annotate.request import framed_rows
from keypoints.derive import Moment, row_indices, session_moments
from server.events import EventEdit
from server.hooks import merge_moments, prompt_moments, read_prompts
from server.routes.paging import DeviceFilter, Page
from server.worker import Priority

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

EventId = Annotated[str, PathParam(pattern=EVENT_ID.pattern, max_length=128)]


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
    # The sequence this row is in, from 0: the ``annotate.chunk_size`` rows
    # captioned together, and the model's description of what was on screen
    # through them; None until annotated.
    sequence: int
    sequence_caption: Optional[str]
    # Index name -> 0..1 for this row (``keypoints.derive``); empty until Jev has answered.
    indices: dict[str, float] = {}
    # The moments starting at this row; the timeline draws them as dividers.
    moments: list[Moment] = []


class EventPatch(BaseModel):
    # null removes the user's caption, showing the model's again.
    caption: Optional[str]


def model_captions(state: State, session_id: str,
                   aggregations: list[Aggregation]) -> tuple[dict[int, str], dict[int, str]]:
    """The model's caption of each row its activity captions cover, by row (frames as in
    ``framed_rows``), and its description of each sequence, by sequence."""
    chunk_size = state.annotate.chunk_size
    captions = {}
    sequences = {}
    for chunk in range(-(-len(aggregations) // chunk_size)):
        try:
            caption = state.captions.get(session_id, chunk)
        except ValidationError as exc:
            logger.warning("skipping the activity caption of %s chunk %d: %s",
                           session_id, chunk, exc)
            continue
        if caption is None or caption.annotation is None:
            continue
        sequences[chunk] = caption.annotation.dense_caption
        framed = framed_rows(aggregations, chunk, chunk_size)
        for action in caption.annotation.actions:
            for row in framed[action.start - 1:action.end]:
                captions[row] = action.caption
    return captions, sequences


def session_events(state: State, session_id: str,
                   include_deleted: bool = False) -> dict[int, Event]:
    """The events of one session, by row; none if it has no rows.

    Moments are found over every answered row, deleted ones included, so
    deleting an event does not start a new moment at the next one. The prompts
    Claude Code reported on this device are added where they fall (``server.hooks``).
    """
    aggregations = state.events.rows(session_id)
    if not aggregations:
        return {}
    device_id = session_id.split("-", 1)[0]
    edits = state.events.edits(session_id).rows
    captions, sequences = model_captions(state, session_id, aggregations)
    chunk_size = state.annotate.chunk_size
    answers = state.answers.by_row(session_id)
    moments = merge_moments(
        session_moments(answers, state.questions, state.keypoints.moment_on,
                        state.keypoints.moment_off),
        prompt_moments(aggregations, read_prompts(state.devices_dir / device_id)))
    events = {}
    for row, aggregation in enumerate(aggregations):
        edit = edits.get(row, EventEdit())
        if edit.deleted and not include_deleted:
            continue
        event_id = event_id_of(session_id, row)
        events[row] = Event(
            id=event_id, device_id=device_id, timestamp=aggregation.timestamp,
            screenshot_url=(f"/events/{event_id}/screenshot"
                            if aggregation.screenshot_path else None),
            inputs=[{"type": e.event_type, "timestamp": e.timestamp, **e.details}
                    for e in aggregation.events],
            caption=edit.caption if edit.caption is not None else captions.get(row),
            sequence=row // chunk_size, sequence_caption=sequences.get(row // chunk_size),
            indices=row_indices(answers.get(row, {}), state.questions),
            moments=moments.get(row, []))
    return events


def overlaps(aggregations: list[Aggregation], since: Optional[float],
             until: Optional[float]) -> bool:
    """Whether a session's rows have a timestamp in ``[since, until]``; None leaves that side open."""
    if not aggregations:
        return False
    timestamps = [aggregation.timestamp for aggregation in aggregations]
    return ((since is None or max(timestamps) >= since)
            and (until is None or min(timestamps) <= until))


def sessions_between(state: State, device: Optional[str], since: Optional[float],
                     until: Optional[float]) -> list[str]:
    """The stored sessions, of one device or of all, with a row in ``[since, until]``."""
    return [session_id for session_id in state.events.sessions(device)
            if overlaps(state.events.rows(session_id), since, until)]


def stored_events(state: State, device: Optional[str] = None, since: Optional[float] = None,
                  until: Optional[float] = None) -> list[Event]:
    """The events the user has not deleted, of one device or of all, in no particular order.

    Only sessions with a row in ``[since, until]`` are built; events of those
    sessions outside it are left in, for the caller to filter.
    """
    return [event for session_id in sessions_between(state, device, since, until)
            for event in session_events(state, session_id).values()]


def stored_timestamps(state: State, device: Optional[str] = None) -> list[float]:
    """The timestamp of every event the user has not deleted, of one device or of all."""
    timestamps = []
    for session_id in state.events.sessions(device):
        edits = state.events.edits(session_id).rows
        timestamps += [aggregation.timestamp
                       for row, aggregation in enumerate(state.events.rows(session_id))
                       if not edits.get(row, EventEdit()).deleted]
    return timestamps


def locate(event_id: str) -> tuple[str, int]:
    """The session id and row of ``event_id``."""
    match = EVENT_ID.match(event_id)
    return match.group("session_id"), int(match.group("row"))


def find(request: Request, event_id: str, include_deleted: bool = False) -> Event:
    session_id, row = locate(event_id)
    event = session_events(request.app.state, session_id, include_deleted).get(row)
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
    events = [event for event in stored_events(request.app.state, device, since, until)
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


class Activity(BaseModel):
    edges: list[float]
    counts: list[int]


# The most edges one request may count between.
MAX_EDGES = 400


def parse_edges(text: str) -> list[float]:
    """The edges of ``GET /events/activity``: at least two unix seconds, ascending."""
    try:
        edges = [float(part) for part in text.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="edges must be unix seconds separated by commas")
    if len(edges) < 2 or len(edges) > MAX_EDGES:
        raise HTTPException(status_code=400, detail=f"edges must be 2 to {MAX_EDGES} values")
    if any(b <= a for a, b in zip(edges, edges[1:])):
        raise HTTPException(status_code=400, detail="edges must be ascending")
    return edges


def count_between(timestamps: list[float], edges: list[float]) -> list[int]:
    """How many of the sorted ``timestamps`` fall in each ``[edges[i], edges[i + 1])``."""
    at = [bisect.bisect_left(timestamps, edge) for edge in edges]
    return [b - a for a, b in zip(at, at[1:])]


@router.get("/activity")
def activity(
    request: Request,
    edges: Annotated[str, Query(description="Unix seconds, ascending, separated by commas")],
    device: DeviceFilter = None,
) -> Activity:
    """How many events fall in each span between consecutive edges, every device's or one device's."""
    bounds = parse_edges(edges)
    timestamps = sorted(stored_timestamps(request.app.state, device))
    return Activity(edges=bounds, counts=count_between(timestamps, bounds))


@router.get("/{event_id}")
def get_event(event_id: EventId, request: Request) -> Event:
    return find(request, event_id)


@router.patch("/{event_id}")
def update_event(event_id: EventId, body: EventPatch, request: Request) -> Event:
    """Set the event's caption; the model's stays stored and returns if this is null."""
    find(request, event_id)
    request.app.state.events.set_caption(*locate(event_id), body.caption)
    return find(request, event_id)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: EventId, request: Request) -> None:
    """Hide the event from ``/events``; its row and screenshot stay stored."""
    find(request, event_id)
    request.app.state.events.hide(*locate(event_id))


@router.put("/{event_id}")
def upload_event(event_id: EventId, request: Request, response: Response,
                 aggregation: Annotated[str, Form()],
                 screenshot: Optional[UploadFile] = None,
                 annotate: bool = True) -> Event:
    """Store the next row of a session, and its screenshot; safe to repeat.

    201 when the row is stored. 200, storing nothing, if the session already
    has that row. 409 if rows before it are missing; ``expected_row`` in the
    detail is the one to send next. The first row of a chunk has the chunk
    before it captioned. With ``annotate`` false the row is only stored; with
    ``annotate.backfill`` the server annotates its chunk later.
    """
    state = request.app.state
    session_id, row = locate(event_id)
    try:
        record = json.loads(aggregation)
        Aggregation.model_validate(record)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"not an aggregation row: {exc}")

    appended = state.events.append(session_id, row, record,
                                   screenshot.file.read() if screenshot is not None else None)
    if appended.outcome == "gap":
        raise HTTPException(status_code=409, detail={
            "message": f"session {session_id} has {appended.rows} rows; send row {appended.rows} next",
            "expected_row": appended.rows})
    if appended.outcome == "duplicate":
        return find(request, event_id, include_deleted=True)

    chunk_size = state.annotate.chunk_size
    # A chunk is captioned once the row after it is stored: that row's
    # screenshot bounds the inputs of the chunk's last row
    # (``juno.sessions.regroup_inputs``). A session's last chunk is found by
    # ``find_work`` once the session has gone quiet.
    if annotate and row > 0 and row % chunk_size == 0:
        state.worker.request(session_id, row // chunk_size - 1, Priority.LIVE)
    response.status_code = 201
    return find(request, event_id, include_deleted=True)


@router.get("/{event_id}/screenshot")
def get_screenshot(event_id: EventId, request: Request) -> FileResponse:
    find(request, event_id)
    path = request.app.state.events.screenshot(*locate(event_id))
    if path is None:
        raise HTTPException(status_code=404, detail=f"{event_id} has no screenshot")
    return FileResponse(path, media_type="image/png")
