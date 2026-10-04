"""The stored atoms: one ``Aggregation`` per row of a session's ``raw_aggregations.jsonl``.

This is everything the recorder kept. What a model is shown is derived from
it in ``annotate.request``.

The server keeps the sessions of every device under one directory, as
``<devices dir>/<device id>/sessions/<session name>/``. A session's id there is
``<device id>-<session name>``; the device id is lowercase letters and digits,
so the first hyphen ends it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel

AGGREGATIONS_FILE = "raw_aggregations.jsonl"
DEVICE_ID_PATTERN = r"[a-z0-9]+"
SESSION_ID_PATTERN = DEVICE_ID_PATTERN + r"-[A-Za-z0-9_]+"


def session_dir(devices_dir: Path, session_id: str) -> Path:
    """Where the session with id ``session_id`` is stored under ``devices_dir``."""
    device_id, name = session_id.split("-", 1)
    return devices_dir / device_id / "sessions" / name


def session_id_of(directory: Path) -> str:
    """The id of the session stored in ``directory``."""
    return f"{directory.parent.parent.name}-{directory.name}"


def session_dirs(devices_dir: Path, device_id: Optional[str] = None) -> list[Path]:
    """The stored session directories, of one device or of all, ordered by id."""
    found = devices_dir.glob(f"{device_id or '*'}/sessions/*")
    return sorted((p for p in found if p.is_dir()), key=session_id_of)


def count_rows(directory: Path) -> int:
    """How many aggregations the session in ``directory`` holds so far."""
    try:
        with open(directory / AGGREGATIONS_FILE, "rb") as rows:
            return sum(1 for line in rows if line.strip())
    except FileNotFoundError:
        return 0


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
