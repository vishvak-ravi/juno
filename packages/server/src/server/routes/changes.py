"""``/changes``: what changed in the sessions since a client last asked, for a view that stays open.

``GET /changes?cursor=&since=&until=&device=&events=`` lists the sessions, as
``/sessions`` does, with an event between ``since`` and ``until`` that changed
after ``cursor``: a row uploaded, a caption or Jev's answers stored, a prompt
reported, an event edited or deleted. The reply's ``cursor`` goes in the next
call. Without a cursor, or with one from an earlier run of the server, every
session in the range is listed.

``events`` names sessions (ids separated by commas) whose events the client
holds. Each of those listed carries the events that differ since the cursor,
oldest first, and the ids of the events deleted since; or every event, marked
``whole``, when the session was first seen or last changed too long ago for
the difference to be known (``server.versions``).
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from juno.events import event_id_of
from server.routes.events import Event, locate, sessions_between
from server.routes.paging import DeviceFilter
from server.routes.sessions import Session, session_of

router = APIRouter(prefix="/changes", tags=["changes"])


class SessionChange(Session):
    # The events that differ since the cursor, oldest first; None when the
    # session was not named in ``events``.
    events: Optional[list[Event]] = None
    # Ids of the events deleted since the cursor.
    removed: list[str] = []
    # ``events`` is every event of the session, not only the changed ones.
    whole: bool = False


class Changes(BaseModel):
    cursor: str
    # Newest first.
    sessions: list[SessionChange]


@router.get("")
def get_changes(
    request: Request,
    cursor: Optional[str] = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    device: DeviceFilter = None,
    events: Annotated[Optional[str], Query(description="Session ids separated by commas")] = None,
) -> Changes:
    """The sessions in the range changed after ``cursor``, with their changed events where asked."""
    state = request.app.state
    versions = state.versions
    after = versions.parse(cursor)
    wanted = set(events.split(",")) if events else set()
    seen = after or 0
    changed = []
    for session_id in sessions_between(state, device, since, until):
        record = versions.look(session_id)
        seen = max(seen, record.seq)
        if after is not None and record.seq <= after:
            continue
        session = session_of(session_id, state)
        if session is None or (since is not None and session.end < since) \
                or (until is not None and session.start > until):
            continue
        change = SessionChange(**session.model_dump(exclude={"events"}))
        if session_id in wanted:
            rows = None if after is None else versions.changed_rows(session_id, after)
            if rows is None:
                change.events = session.events
                change.whole = True
            else:
                changed_rows, removed_rows = rows
                change.events = [e for e in session.events if locate(e.id)[1] in changed_rows]
                change.removed = [event_id_of(session_id, row) for row in sorted(removed_rows)]
        changed.append(change)
    changed.sort(key=lambda s: (s.start, s.id), reverse=True)
    return Changes(cursor=versions.cursor(seen), sessions=changed)
