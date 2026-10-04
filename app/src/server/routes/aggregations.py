"""``/aggregations``: the recorded atoms, each one screenshot and the input after it.

Aggregation ``<session id>-<row as six digits>`` is row ``row`` (from 0) of that
session's ``raw_aggregations.jsonl``; ``/aggregations/{id}/screenshot`` is its PNG.

A recorder uploads its session one row at a time, in order, with
``PUT /aggregations/{id}``: a multipart body whose ``aggregation`` part is the
row as the recorder wrote it (JSON) and whose ``screenshot`` part is the PNG
that row points to. The server keeps its own copy of both, and queues the
annotation of each chunk the moment its last row arrives, unless the request
says ``?annotate=false``.
"""

import json
import re
import threading
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import ValidationError

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
from server.routes.annotations import queue_chunk
from server.routes.paging import DeviceFilter, Page, page_of
from server.worker import Priority

router = APIRouter(prefix="/aggregations", tags=["aggregations"])

AGGREGATION_ID = re.compile(rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<row>\d{{6,}})$")
AggregationId = Annotated[str, PathParam(pattern=AGGREGATION_ID.pattern, max_length=128)]

SCREENSHOTS_DIR = "screenshots"

# An upload counts the session's rows, then appends one; two at once must not
# interleave, so uploads run one at a time.
upload_lock = threading.Lock()


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


def find(devices_dir: Path, aggregation_id: str) -> AggregationItem:
    match = AGGREGATION_ID.match(aggregation_id)
    items = session_items(session_dir(devices_dir, match.group("session_id")))
    row = int(match.group("row"))
    if row >= len(items):
        raise HTTPException(status_code=404, detail=f"no aggregation with id {aggregation_id}")
    return items[row]


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


@router.get("/{aggregation_id}")
def get_aggregation(aggregation_id: AggregationId, request: Request) -> AggregationItem:
    return find(request.app.state.devices_dir, aggregation_id)


@router.put("/{aggregation_id}")
def upload_aggregation(aggregation_id: AggregationId, request: Request, response: Response,
                       aggregation: Annotated[str, Form()],
                       screenshot: Optional[UploadFile] = None,
                       annotate: bool = True) -> AggregationItem:
    """Store the next row of a session, and its screenshot; safe to repeat.

    201 when the row is stored. 200, storing nothing, if the session already
    has that row. 409 if rows before it are missing; ``expected_row`` in the
    detail is the one to send next. With ``annotate`` false the row is only
    stored; its chunk can be annotated later with ``PUT /annotations/{id}``.
    """
    state = request.app.state
    match = AGGREGATION_ID.match(aggregation_id)
    session_id, row = match.group("session_id"), int(match.group("row"))
    directory = session_dir(state.devices_dir, session_id)
    try:
        record = json.loads(aggregation)
        Aggregation.model_validate(record)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"not an aggregation row: {exc}")

    with upload_lock:
        stored = count_rows(directory)
        if row < stored:
            return find(state.devices_dir, aggregation_id)
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
        queue_chunk(state, session_id, row // chunk_size, Priority.LIVE)
    response.status_code = 201
    return find(state.devices_dir, aggregation_id)


@router.get("/{aggregation_id}/screenshot")
def get_screenshot(aggregation_id: AggregationId, request: Request) -> FileResponse:
    devices_dir = request.app.state.devices_dir
    item = find(devices_dir, aggregation_id)
    path = Path(item.screenshot_path).resolve() if item.screenshot_path else None
    # Only files stored inside that session are served.
    directory = session_dir(devices_dir, item.session_id).resolve()
    if path is None or not path.is_file() or directory not in path.parents:
        raise HTTPException(status_code=404, detail=f"{aggregation_id} has no screenshot")
    return FileResponse(path, media_type="image/png")
