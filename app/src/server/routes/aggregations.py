"""``/aggregations``: the recorded atoms, each one screenshot and the input after it.

Aggregation ``<session id>-<row as six digits>`` is row ``row`` (from 0) of that
session's ``raw_aggregations.jsonl``; ``/aggregations/{id}/screenshot`` is its PNG.
"""

import re
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from annotate.records import Aggregation, read_aggregations
from server.routes.paging import Page, page_of

router = APIRouter(prefix="/aggregations", tags=["aggregations"])

AGGREGATION_ID = re.compile(r"^(?P<session_id>[A-Za-z0-9_]+)-(?P<row>\d{6,})$")
AggregationId = Annotated[str, PathParam(pattern=AGGREGATION_ID.pattern, max_length=128)]


class AggregationItem(Aggregation):
    id: str
    session_id: str
    row: int


def session_items(session_dir: Path) -> list[AggregationItem]:
    """Every aggregation of one session, in the order written; none if it has no file."""
    try:
        aggregations = read_aggregations(session_dir)
    except FileNotFoundError:
        return []
    return [AggregationItem(id=f"{session_dir.name}-{row:06d}", session_id=session_dir.name,
                            row=row, **aggregation.model_dump())
            for row, aggregation in enumerate(aggregations)]


def find(sessions_dir: Path, aggregation_id: str) -> AggregationItem:
    match = AGGREGATION_ID.match(aggregation_id)
    items = session_items(sessions_dir / match.group("session_id"))
    row = int(match.group("row"))
    if row >= len(items):
        raise HTTPException(status_code=404, detail=f"no aggregation with id {aggregation_id}")
    return items[row]


@router.get("")
def list_aggregations(
    request: Request,
    session_id: Optional[str] = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[AggregationItem]:
    """Aggregations, newest first by default, optionally of one session or a time range."""
    sessions_dir = request.app.state.sessions_dir
    directories = sorted(sessions_dir.glob(session_id or "*"))
    items = [item for directory in directories for item in session_items(directory)
             if (since is None or item.timestamp >= since)
             and (until is None or item.timestamp <= until)]
    return page_of(items, limit, order, after)


@router.get("/{aggregation_id}")
def get_aggregation(aggregation_id: AggregationId, request: Request) -> AggregationItem:
    return find(request.app.state.sessions_dir, aggregation_id)


@router.get("/{aggregation_id}/screenshot")
def get_screenshot(aggregation_id: AggregationId, request: Request) -> FileResponse:
    sessions_dir = request.app.state.sessions_dir
    item = find(sessions_dir, aggregation_id)
    path = Path(item.screenshot_path).resolve() if item.screenshot_path else None
    # Only files the recorder put inside that session are served.
    session_dir = (sessions_dir / item.session_id).resolve()
    if path is None or not path.is_file() or session_dir not in path.parents:
        raise HTTPException(status_code=404, detail=f"{aggregation_id} has no screenshot")
    return FileResponse(path, media_type="image/png")
