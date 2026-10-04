"""``/sessions`` and ``/sessions/{id}``: the sessions uploaded so far, one directory each."""

from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from annotate.records import (
    AGGREGATIONS_FILE,
    SESSION_ID_PATTERN,
    count_rows,
    session_dir,
    session_dirs,
    session_id_of,
)
from annotate.store import ANNOTATIONS_DIR
from server.routes.paging import DeviceFilter, Page, page_of

router = APIRouter(prefix="/sessions", tags=["sessions"])

SessionId = Annotated[str, PathParam(pattern=f"^{SESSION_ID_PATTERN}$", max_length=96)]


class Session(BaseModel):
    # ``<device id>-session_<date>_<time>``, the time being when recording started.
    id: str
    device_id: str
    aggregations: int
    # Annotations requested so far, whatever their status.
    annotations: int
    # Unix seconds of the last aggregation written; None if there is none yet.
    updated_at: Optional[float]


def describe(directory: Path) -> Session:
    try:
        updated_at = (directory / AGGREGATIONS_FILE).stat().st_mtime
    except FileNotFoundError:
        updated_at = None
    session_id = session_id_of(directory)
    return Session(id=session_id, device_id=session_id.split("-", 1)[0],
                   aggregations=count_rows(directory),
                   annotations=len(list((directory / ANNOTATIONS_DIR).glob("*.json"))),
                   updated_at=updated_at)


@router.get("")
def list_sessions(
    request: Request,
    device: DeviceFilter = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[Session]:
    """Stored sessions, optionally of one device, by id; the last first by default."""
    directories = session_dirs(request.app.state.devices_dir, device)
    return page_of([describe(directory) for directory in directories], limit, order, after)


@router.get("/{session_id}")
def get_session(session_id: SessionId, request: Request) -> Session:
    directory = session_dir(request.app.state.devices_dir, session_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"no session with id {session_id}")
    return describe(directory)
