"""``/sessions``: each device's recordings as wholes, one session at a time.

A session is every event of one recorder run (``/events`` merges them into one
list). ``GET /sessions`` lists the sessions with an event between ``since`` and
``until``, newest first by default, ordered by ``(start, id)``: each with its
first and last event's time and how many events it has, without the events.
``GET /sessions/{id}`` is one session with every event, oldest first, as
``/events`` returns them. Deleted events are left out of both.
"""

from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel
from starlette.datastructures import State

from juno.sessions import SESSION_ID_PATTERN, session_dir, session_dirs, session_id_of
from server.routes.events import Event, session_events
from server.routes.paging import DeviceFilter, Page, page_of

router = APIRouter(prefix="/sessions", tags=["sessions"])

SessionId = Annotated[str, PathParam(pattern=f"^{SESSION_ID_PATTERN}$", max_length=128)]


class Session(BaseModel):
    id: str
    device_id: str
    # Unix seconds of the first and last event, by the recording device's clock.
    start: float
    end: float
    event_count: int
    # Events with a caption, the user's or the model's.
    captioned: int


class SessionDetail(Session):
    # Oldest first.
    events: list[Event]


def session_of(directory: Path, state: State) -> Optional[SessionDetail]:
    """The session stored in ``directory`` with its events; None if it has none."""
    events = sorted(session_events(directory, state.annotate.chunk_size, state.keypoints,
                                   state.questions).values(),
                    key=lambda e: (e.timestamp, e.id))
    if not events:
        return None
    return SessionDetail(id=session_id_of(directory), device_id=events[0].device_id,
                         start=events[0].timestamp, end=events[-1].timestamp,
                         event_count=len(events),
                         captioned=sum(1 for e in events if e.caption is not None),
                         events=events)


@router.get("")
def list_sessions(
    request: Request,
    device: DeviceFilter = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[Session]:
    """Sessions with an event in the time range, newest first by default, without their events."""
    state = request.app.state
    found = [session_of(directory, state)
             for directory in session_dirs(state.devices_dir, device)]
    sessions = sorted((Session.model_validate(s.model_dump(exclude={"events"})) for s in found
                       if s is not None
                       and (since is None or s.end >= since)
                       and (until is None or s.start <= until)),
                      key=lambda s: (s.start, s.id))
    return page_of(sessions, limit, order, after)


@router.get("/{session_id}")
def get_session(session_id: SessionId, request: Request) -> SessionDetail:
    state = request.app.state
    session = session_of(session_dir(state.devices_dir, session_id), state)
    if session is None:
        raise HTTPException(status_code=404, detail=f"no session with id {session_id}")
    return session
