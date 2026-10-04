"""The stored atoms: one ``Aggregation`` per row of a session's ``raw_aggregations.jsonl``.

This is everything the recorder kept. What a model is shown is derived from
it in ``annotate.request``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel

AGGREGATIONS_FILE = "raw_aggregations.jsonl"


class InputEvent(BaseModel):
    timestamp: float
    monitor_index: int
    monitor: Optional[dict[str, int]]
    # mouse_down | mouse_up | mouse_scroll | mouse_move | key_press | key_release
    event_type: str
    details: dict[str, Any]
    # Desktop coordinates, in the space of ``monitor``.
    cursor_position: Optional[tuple[int, int]]


class Aggregation(BaseModel):
    """A burst boundary: one screenshot and the input events that follow it."""

    timestamp: float
    reason: str
    event_type: str
    request_state: str
    screenshot_path: Optional[str]
    screenshot_timestamp: Optional[float]
    end_screenshot_timestamp: Optional[float]
    num_events: int
    events: list[InputEvent]
    cursor_position: Optional[tuple[int, int]]
    monitor: Optional[dict[str, int]]
    burst_id: Optional[int]
    scale_factor: Optional[float]


def read_aggregations(session_dir: Union[str, Path]) -> list[Aggregation]:
    """Every aggregation of a recorded session, in the order it was written."""
    lines = (Path(session_dir) / AGGREGATIONS_FILE).read_text().splitlines()
    return [Aggregation.model_validate_json(line) for line in lines if line.strip()]
