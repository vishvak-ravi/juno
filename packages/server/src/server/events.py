"""The events the server stores: every row a recorder uploads, its screenshot, and the user's edits.

``Events`` is what the routes and workers read and write through;
``FileEvents`` keeps them on disk. Session ``<device id>-<name>`` is stored in
``<devices dir>/<device id>/sessions/<name>/`` (``juno.sessions.session_dir``) as

- ``event_rows.jsonl``: one row per line, in upload order, with every field the
  recorder wrote;
- ``screenshots/<recorder's file name>``: the PNG a row names. The row stores
  its path relative to the session directory; ``rows`` hands it out absolute;
- ``event_edits.json``: the user's captions and deletions, by row.

``FileEvents`` keeps each session's parsed rows in memory with the size and
modification time of its ``event_rows.jsonl``, and parses the file again
only when either differs.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Literal, Optional, Protocol

from pydantic import BaseModel

from juno.sessions import (
    Aggregation,
    replace_file,
    resolved,
    session_dir,
    session_dirs,
    session_id_of,
    stored_path,
)

EVENT_ROWS_FILE = "event_rows.jsonl"
SCREENSHOTS_DIR = "screenshots"
EDITS_FILE = "event_edits.json"


class EventEdit(BaseModel):
    """What the user changed on one event."""
    caption: Optional[str] = None
    deleted: bool = False


class SessionEdits(BaseModel):
    # By row.
    rows: dict[int, EventEdit] = {}


class Appended(BaseModel):
    # stored: the row is new. duplicate: the server had it already. gap: rows
    # before it are missing, and nothing was stored.
    outcome: Literal["stored", "duplicate", "gap"]
    # How many rows the session holds now: the row to send next.
    rows: int


class Events(Protocol):

    def devices(self) -> list[str]:
        """Every device with a stored session."""

    def sessions(self, device_id: Optional[str] = None) -> list[str]:
        """The stored sessions, of one device or of all, ordered by id."""

    def rows(self, session_id: str) -> list[Aggregation]:
        """Every row of a session in upload order, screenshot paths absolute; empty if none.

        Callers share the list and its rows and must not change them.
        """

    def row_count(self, session_id: str) -> int: ...

    def screenshot(self, session_id: str, row: int) -> Optional[Path]:
        """The stored screenshot file of a row; None if it has none or the row does not exist."""

    def append(self, session_id: str, row: int, record: dict,
               screenshot: Optional[bytes]) -> Appended:
        """Store row ``row`` of a session and its screenshot if it is the next row."""

    def edits(self, session_id: str) -> SessionEdits: ...

    def set_caption(self, session_id: str, row: int, caption: Optional[str]) -> None:
        """Set the user's caption of a row; None shows the model's again."""

    def hide(self, session_id: str, row: int) -> None:
        """Leave a row out of what the API lists; it stays stored."""


def read_rows(directory: Path) -> list[Aggregation]:
    """The rows stored in a session directory, screenshot paths absolute."""
    try:
        lines = (directory / EVENT_ROWS_FILE).read_text().splitlines()
    except FileNotFoundError:
        return []
    rows = [Aggregation.model_validate_json(line) for line in lines if line.strip()]
    for row in rows:
        row.screenshot_path = resolved(directory, row.screenshot_path)
    return rows


def count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as lines:
            return sum(1 for line in lines if line.strip())
    except FileNotFoundError:
        return 0


class FileEvents:

    def __init__(self, devices_dir: Path):
        self.devices_dir = devices_dir
        # An append counts the session's rows, then adds one; two at once must
        # not interleave. An edit reads, changes and replaces the edits file.
        self._append_lock = threading.Lock()
        self._edit_lock = threading.Lock()
        # By session id: (size, mtime_ns) of its rows file, and the rows parsed from it.
        self._rows: dict[str, tuple[tuple[int, int], list[Aggregation]]] = {}

    def devices(self) -> list[str]:
        return sorted({session_id.split("-", 1)[0] for session_id in self.sessions()})

    def sessions(self, device_id: Optional[str] = None) -> list[str]:
        return [session_id_of(directory) for directory in session_dirs(self.devices_dir, device_id)]

    def rows(self, session_id: str) -> list[Aggregation]:
        directory = session_dir(self.devices_dir, session_id)
        try:
            stat = (directory / EVENT_ROWS_FILE).stat()
        except FileNotFoundError:
            self._rows.pop(session_id, None)
            return []
        key = (stat.st_size, stat.st_mtime_ns)
        cached = self._rows.get(session_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        rows = read_rows(directory)
        self._rows[session_id] = (key, rows)
        return rows

    def row_count(self, session_id: str) -> int:
        return count_lines(session_dir(self.devices_dir, session_id) / EVENT_ROWS_FILE)

    def screenshot(self, session_id: str, row: int) -> Optional[Path]:
        directory = session_dir(self.devices_dir, session_id)
        rows = self.rows(session_id)
        if not 0 <= row < len(rows) or rows[row].screenshot_path is None:
            return None
        path = Path(rows[row].screenshot_path).resolve()
        # Only files stored inside the session are handed out.
        if not path.is_file() or directory.resolve() not in path.parents:
            return None
        return path

    def append(self, session_id: str, row: int, record: dict,
               screenshot: Optional[bytes]) -> Appended:
        directory = session_dir(self.devices_dir, session_id)
        with self._append_lock:
            stored = count_lines(directory / EVENT_ROWS_FILE)
            if row < stored:
                return Appended(outcome="duplicate", rows=stored)
            if row > stored:
                return Appended(outcome="gap", rows=stored)
            # The row arrives naming the recorder's file; it is stored naming
            # this server's copy, or nothing if no screenshot came with it.
            path = None
            if screenshot is not None and record.get("screenshot_path"):
                # The file name of either platform's path: no directory part survives.
                name = re.split(r"[\\/]", record["screenshot_path"])[-1]
                path = directory / SCREENSHOTS_DIR / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(screenshot)
            record = {**record, "screenshot_path": stored_path(directory, str(path) if path else None)}
            directory.mkdir(parents=True, exist_ok=True)
            with open(directory / EVENT_ROWS_FILE, "a") as rows:
                rows.write(json.dumps(record) + "\n")
            return Appended(outcome="stored", rows=stored + 1)

    def edits(self, session_id: str) -> SessionEdits:
        try:
            return SessionEdits.model_validate_json(
                (session_dir(self.devices_dir, session_id) / EDITS_FILE).read_text())
        except FileNotFoundError:
            return SessionEdits()

    def set_caption(self, session_id: str, row: int, caption: Optional[str]) -> None:
        with self._edit_lock:
            edits = self.edits(session_id)
            edits.rows.setdefault(row, EventEdit()).caption = caption
            self._write_edits(session_id, edits)

    def hide(self, session_id: str, row: int) -> None:
        with self._edit_lock:
            edits = self.edits(session_id)
            edits.rows.setdefault(row, EventEdit()).deleted = True
            self._write_edits(session_id, edits)

    def _write_edits(self, session_id: str, edits: SessionEdits) -> None:
        replace_file(session_dir(self.devices_dir, session_id) / EDITS_FILE,
                     edits.model_dump_json(indent=2))
