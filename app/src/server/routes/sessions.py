"""``/sessions`` and ``/sessions/{id}``: what the recorder has written, one directory each."""

from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from annotate.records import AGGREGATIONS_FILE
from annotate.store import ANNOTATIONS_DIR
from server.routes.annotations import count_rows
from server.routes.paging import Page, page_of

router = APIRouter(prefix="/sessions", tags=["sessions"])

# The id is a directory name, so it may not contain dots or slashes.
SessionId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_]+$", max_length=64)]


class Session(BaseModel):
    # The directory name, ``session_<date>_<time>`` of when recording started.
    id: str
    aggregations: int
    # Annotations requested so far, whatever their status.
    annotations: int
    # Unix seconds of the last aggregation written; None if there is none yet.
    updated_at: Optional[float]


def describe(session_dir: Path) -> Session:
    try:
        updated_at = (session_dir / AGGREGATIONS_FILE).stat().st_mtime
    except FileNotFoundError:
        updated_at = None
    return Session(id=session_dir.name, aggregations=count_rows(session_dir),
                   annotations=len(list((session_dir / ANNOTATIONS_DIR).glob("*.json"))),
                   updated_at=updated_at)


@router.get("")
def list_sessions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[Session]:
    """Recorded sessions, newest first by default."""
    directories = sorted(p for p in request.app.state.sessions_dir.glob("*") if p.is_dir())
    return page_of([describe(directory) for directory in directories], limit, order, after)


@router.get("/{session_id}")
def get_session(session_id: SessionId, request: Request) -> Session:
    session_dir = request.app.state.sessions_dir / session_id
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"no session with id {session_id}")
    return describe(session_dir)
