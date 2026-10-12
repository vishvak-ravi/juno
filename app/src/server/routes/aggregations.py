"""``/aggregations``: a session's rows as the recorder wrote them, for the web pages.

Aggregation ``<session id>-<row as six digits>`` is row ``row`` (from 0) of that
session's ``raw_aggregations.jsonl``, and is the same row as the event with that
id: uploads, single rows and screenshots are under ``/events``.
"""

from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Query, Request

from annotate.records import (
    Aggregation,
    read_aggregations,
    session_dirs,
    session_id_of,
)
from server.routes.paging import DeviceFilter, Page, page_of

router = APIRouter(prefix="/aggregations", tags=["aggregations"])


class AggregationItem(Aggregation):
    id: str
    session_id: str
    row: int


def session_items(directory: Path) -> list[AggregationItem]:
    """Every aggregation of one session, in the order written; none if it has no file."""
    try:
        aggregations = read_aggregations(directory)
    except FileNotFoundError:
        return []
    session_id = session_id_of(directory)
    return [AggregationItem(id=f"{session_id}-{row:06d}", session_id=session_id,
                            row=row, **aggregation.model_dump())
            for row, aggregation in enumerate(aggregations)]


@router.get("")
def list_aggregations(
    request: Request,
    session_id: Optional[str] = None,
    device: DeviceFilter = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[AggregationItem]:
    """Aggregations, newest first by default, optionally of one session, device or time range."""
    directories = [directory for directory in session_dirs(request.app.state.devices_dir, device)
                   if session_id is None or session_id_of(directory) == session_id]
    items = [item for directory in directories for item in session_items(directory)
             if (since is None or item.timestamp >= since)
             and (until is None or item.timestamp <= until)]
    return page_of(items, limit, order, after)
