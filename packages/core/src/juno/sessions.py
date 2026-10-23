"""The stored atoms: one ``Aggregation`` per row of a session.

The recorder writes the rows to ``raw_aggregations.jsonl``; the server stores
its copy as ``event_rows.jsonl`` (``server.events``). A row is everything the
recorder kept. What a model is shown is derived from it in ``annotate.request``.

Both files are read through ``parse_rows``, which hands each input event to the
row whose screenshot precedes it (``regroup_inputs``). The recorder writes a
row's events while the burst after it may still be arriving: whenever the row
is the last one it has, it writes the row with no ``end_screenshot_timestamp``
and with every input that has arrived so far, including the inputs of the next
burst, whose own row then starts empty. Regrouping at read time gives every
reader the rows napsack's own rule (``screenshot <= input < next screenshot``)
describes, and leaves the files as the recorder wrote them.

The server keeps the sessions of every device under one directory, as
``<devices dir>/<device id>/sessions/<session name>/``. A session's id there is
``<device id>-<session name>``; the device id is lowercase letters and digits,
so the first hyphen ends it. A screenshot path the server stores is relative to
the session directory (``stored_path``) and is handed out absolute (``resolved``).
"""

from __future__ import annotations

import os
import threading
from bisect import bisect_right
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


def resolved(directory: Path, path: Optional[str]) -> Optional[str]:
    """A stored screenshot path made absolute against its session directory."""
    return None if path is None else str(directory / path)


def stored_path(directory: Path, path: Optional[str]) -> Optional[str]:
    """A screenshot path as stored: relative if it is inside ``directory``, else as given."""
    if path is None:
        return None
    try:
        return str(Path(path).relative_to(directory))
    except ValueError:
        return path


def replace_file(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` in one step, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Its own name per writer: two threads may write the same file at once.
    temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


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


def regroup_inputs(rows: list[Aggregation]) -> list[Aggregation]:
    """``rows`` with each input event in the row whose screenshot precedes it.

    A row's events are the inputs from its screenshot time up to the next row's
    screenshot time, rows taken in screenshot order; an input at exactly a
    screenshot's time belongs to that row, and when two rows share a time, to
    the later one, whose window the earlier one's (empty) window ends at.
    Inputs before the first screenshot stay in the first row. The rows keep
    their order and index; ``num_events`` and ``cursor_position`` follow the
    events, as the recorder derives them. Returns new rows; ``rows`` is unchanged.
    """
    if not rows:
        return rows
    starts = sorted((row.screenshot_timestamp if row.screenshot_timestamp is not None
                     else row.timestamp, index) for index, row in enumerate(rows))
    times = [start for start, _ in starts]
    grouped: list[list[InputEvent]] = [[] for _ in rows]
    for event in sorted((event for row in rows for event in row.events),
                        key=lambda event: event.timestamp):
        position = max(0, bisect_right(times, event.timestamp) - 1)
        grouped[starts[position][1]].append(event)
    return [row.model_copy(update={
        "events": events, "num_events": len(events),
        "cursor_position": events[0].cursor_position if events else None,
    }) for row, events in zip(rows, grouped)]


def parse_rows(text: str) -> list[Aggregation]:
    """The rows in a JSON-lines file's text, inputs regrouped (``regroup_inputs``)."""
    return regroup_inputs([Aggregation.model_validate_json(line)
                           for line in text.splitlines() if line.strip()])


def read_aggregations(session_dir: Union[str, Path]) -> list[Aggregation]:
    """Every aggregation of a recorded session, in the order it was written."""
    return parse_rows((Path(session_dir) / AGGREGATIONS_FILE).read_text())
