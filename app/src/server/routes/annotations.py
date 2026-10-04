"""``/annotations`` and ``/annotations/{id}``: the model's description of each session chunk.

An annotation covers one chunk of a session (see ``annotate.store``) and its id
names both, so the caller picks the id: ``PUT /annotations/{id}`` asks for that
chunk to be annotated and returns at once with ``202 Accepted`` and the
annotation as ``pending``; ``GET`` the same URL (the ``Location`` header) to
see it become ``done`` or ``failed``.
"""

from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi import Path as PathParam

from annotate.records import AGGREGATIONS_FILE
from annotate.store import (
    ANNOTATION_ID,
    AnnotationRecord,
    list_records,
    new_record,
    parse_annotation_id,
    read_record,
    write_record,
)
from server.routes.paging import Page, page_of

router = APIRouter(prefix="/annotations", tags=["annotations"])

AnnotationId = Annotated[str, PathParam(pattern=ANNOTATION_ID.pattern, max_length=128)]


def count_rows(session_dir: Path) -> int:
    """How many aggregations the session has recorded so far."""
    try:
        with open(session_dir / AGGREGATIONS_FILE, "rb") as rows:
            return sum(1 for line in rows if line.strip())
    except FileNotFoundError:
        return 0


@router.get("")
def list_annotations(
    request: Request,
    session_id: Optional[str] = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[AnnotationRecord]:
    """Annotations, newest first by default, optionally of one session or a time range."""
    records = list_records(request.app.state.sessions_dir, session_id, since, until)
    return page_of(records, limit, order, after)


@router.get("/{annotation_id}")
def get_annotation(annotation_id: AnnotationId, request: Request) -> AnnotationRecord:
    session_id, chunk = parse_annotation_id(annotation_id)
    record = read_record(request.app.state.sessions_dir / session_id, chunk)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no annotation with id {annotation_id}")
    return record


@router.put("/{annotation_id}")
def request_annotation(annotation_id: AnnotationId, request: Request,
                       response: Response) -> AnnotationRecord:
    """Ask for the chunk to be annotated; safe to repeat.

    200 with the annotation if it is already done. Otherwise 202: the chunk is
    queued (again, if it had failed) and the annotation is returned as pending.
    404 if the session has not recorded any row of that chunk.
    """
    state = request.app.state
    session_id, chunk = parse_annotation_id(annotation_id)
    session_dir = state.sessions_dir / session_id
    if count_rows(session_dir) <= chunk * state.annotate.chunk_size:
        raise HTTPException(status_code=404,
                            detail=f"session {session_id} has no chunk {chunk}")

    record = read_record(session_dir, chunk)
    if record is not None and record.status == "done":
        return record
    if record is None or not state.worker.is_waiting(session_id, chunk):
        # New, failed, or left pending by a server that stopped: (re)queue it.
        record = new_record(session_id, chunk, state.annotate.model)
        write_record(session_dir, record)
        state.worker.submit(session_id, chunk)
    response.status_code = 202
    response.headers["Location"] = f"/annotations/{annotation_id}"
    return record
