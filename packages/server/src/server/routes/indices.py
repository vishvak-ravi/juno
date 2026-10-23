"""``/indices``: each index's mean over time, for a chart.

The events between ``since`` and ``until`` (of one device with ``device``) are
put in buckets of ``bucket_seconds``, each starting at a multiple of it since
the Unix epoch, and each index is averaged over the bucket's events Jev has
answered. Buckets with no events are left out. Deleted events don't count.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from server.routes.events import Event, stored_events
from server.routes.paging import DeviceFilter

router = APIRouter(prefix="/indices", tags=["indices"])


class Bucket(BaseModel):
    # Unix seconds.
    start: float
    # Events in the bucket.
    rows: int
    # Of those, the events with at least one index.
    detected: int
    # Index name -> mean over the detected events with that index; None if none has it.
    values: dict[str, Optional[float]]


class Indices(BaseModel):
    names: list[str]
    bucket_seconds: float
    # Oldest first.
    buckets: list[Bucket]


def buckets_of(events: list[Event], names: list[str], bucket_seconds: float) -> list[Bucket]:
    grouped: dict[float, list[Event]] = {}
    for event in events:
        grouped.setdefault(event.timestamp // bucket_seconds * bucket_seconds, []).append(event)
    buckets = []
    for start in sorted(grouped):
        members = grouped[start]
        values = {}
        for name in names:
            found = [event.indices[name] for event in members if name in event.indices]
            values[name] = sum(found) / len(found) if found else None
        buckets.append(Bucket(start=start, rows=len(members),
                              detected=sum(1 for event in members if event.indices),
                              values=values))
    return buckets


@router.get("")
def get_indices(
    request: Request,
    device: DeviceFilter = None,
    since: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    until: Annotated[Optional[float], Query(description="Unix seconds")] = None,
    bucket_seconds: Annotated[Optional[float], Query(gt=0, description="default: keypoints.bucket_seconds")] = None,
) -> Indices:
    state = request.app.state
    seconds = bucket_seconds or state.keypoints.bucket_seconds
    events = [event for event in stored_events(state, device)
              if (since is None or event.timestamp >= since)
              and (until is None or event.timestamp <= until)]
    names = list(state.questions.indices)
    return Indices(names=names, bucket_seconds=seconds, buckets=buckets_of(events, names, seconds))
