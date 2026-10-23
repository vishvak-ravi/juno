"""The stored atoms: one ``Aggregation`` per row of a session.

The recorder writes the rows to ``raw_aggregations.jsonl``; the server stores
its copy as ``event_rows.jsonl`` (``server.events``). A row is everything the
recorder kept. What a model is shown is derived from it in ``annotate.request``.

The server keeps the sessions of every device under one directory, as
``<devices dir>/<device id>/sessions/<session name>/``. A session's id there is
``<device id>-<session name>``; the device id is lowercase letters and digits,
so the first hyphen ends it. A screenshot path the server stores is relative to
the session directory (``stored_path``) and is handed out absolute (``resolved``).
"""

from __future__ import annotations

import os
import threading
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


def file_stamp(path: Path) -> Optional[tuple[int, int]]:
    """The size and modification time (ns) of a file, which change with its contents; None if it is not there."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns


def dir_stamps(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """Each file of a directory with its size and modification time, by name; empty if there is no directory."""
    try:
        with os.scandir(directory) as entries:
            stamps = [(entry.name, entry.stat().st_size, entry.stat().st_mtime_ns)
                      for entry in entries if entry.is_file()]
    except FileNotFoundError:
        return ()
    return tuple(sorted(stamps))


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


def read_aggregations(session_dir: Union[str, Path]) -> list[Aggregation]:
    """Every aggregation of a recorded session, in the order it was written."""
    lines = (Path(session_dir) / AGGREGATIONS_FILE).read_text().splitlines()
    return [Aggregation.model_validate_json(line) for line in lines if line.strip()]
