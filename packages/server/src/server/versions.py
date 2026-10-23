"""Which sessions changed since a client last asked, and each one's events built once per change.

A session's fingerprint is the ``version`` of its rows and edits
(``server.events``), its captions (``annotate.store``), its answers
(``keypoints.store``) and the prompts its device reported in its span
(``server.hooks``). ``SessionVersions`` numbers every change it sees: each time
it looks at a session and the fingerprint differs from the one it saw last,
the session takes the next number. A client keeps the last number it was
given (the cursor of ``GET /changes``) and asks for the sessions numbered
after it.

What is built from one fingerprint (``build``, a session's events) is kept
with it, so a session is built again only after it changed. Each rebuild
records which rows differ from the build before, under the session's number,
so a client can be sent those rows alone.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generic, Hashable, Optional, TypeVar

from annotate.store import ActivityCaptions
from keypoints.store import QuestionAnswers
from server.events import Events
from server.hooks import prompts_version

T = TypeVar("T")
Fingerprint = Hashable
# ``diff(before, after)``: the rows whose events differ, and the rows no longer listed.
Diff = Callable[[T, T], tuple[set[int], set[int]]]

# Rebuilds remembered per session; a cursor from before the oldest kept is answered with the whole session.
KEPT_CHANGES = 64


@dataclass
class RowChange:
    # The session's number when the rebuild happened.
    seq: int
    changed: set[int]
    removed: set[int]


@dataclass
class SessionRecord(Generic[T]):
    fingerprint: Fingerprint
    # The number taken at the latest change seen, and at the first look.
    seq: int
    first_seq: int
    build: Optional[T] = None
    built_of: Optional[Fingerprint] = None
    changes: list[RowChange] = field(default_factory=list)
    # The number of the newest change forgotten.
    forgotten_seq: int = 0


def session_fingerprint(events: Events, captions: ActivityCaptions, answers: QuestionAnswers,
                        devices_dir: Path) -> Callable[[str], Fingerprint]:
    """The fingerprint of a session stored in these repositories."""
    def fingerprint(session_id: str) -> Fingerprint:
        rows = events.rows(session_id)
        span = (rows[0].timestamp, rows[-1].timestamp) if rows else None
        device_id = session_id.split("-", 1)[0]
        return (events.version(session_id), captions.version(session_id), answers.version(session_id),
                prompts_version(devices_dir / device_id, *span) if span else None)
    return fingerprint


class SessionVersions(Generic[T]):
    """Numbers each session's changes and keeps what was built from its latest fingerprint."""

    def __init__(self, fingerprint: Callable[[str], Fingerprint]) -> None:
        self._fingerprint = fingerprint
        self._records: dict[str, SessionRecord[T]] = {}
        self._seq = 0
        # Names this run of the server: a cursor from another run is not understood.
        self.epoch = secrets.token_hex(4)
        self._lock = threading.RLock()

    def look(self, session_id: str) -> SessionRecord[T]:
        """Fingerprints the session now; it takes the next number if it changed."""
        fingerprint = self._fingerprint(session_id)
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                self._seq += 1
                record = SessionRecord(fingerprint, self._seq, self._seq)
                self._records[session_id] = record
            elif record.fingerprint != fingerprint:
                self._seq += 1
                record.fingerprint = fingerprint
                record.seq = self._seq
            return record

    def built(self, session_id: str, build: Callable[[], T], diff: Diff) -> T:
        """What ``build`` makes of the session now, made again only after the session changed."""
        with self._lock:
            record = self.look(session_id)
            if record.build is not None and record.built_of == record.fingerprint:
                return record.build
            fresh = build()
            if record.build is not None:
                changed, removed = diff(record.build, fresh)
                record.changes.append(RowChange(record.seq, changed, removed))
                if len(record.changes) > KEPT_CHANGES:
                    forgotten = record.changes[:-KEPT_CHANGES]
                    record.forgotten_seq = forgotten[-1].seq
                    del record.changes[:-KEPT_CHANGES]
            record.build = fresh
            record.built_of = record.fingerprint
            return fresh

    def changed_rows(self, session_id: str, since: int) -> Optional[tuple[set[int], set[int]]]:
        """The rows changed and removed in the session's rebuilds numbered after ``since``.

        None when the session was first seen after ``since`` or a rebuild
        after it has been forgotten: everything may have changed.
        """
        with self._lock:
            record = self._records.get(session_id)
            if record is None or since < record.first_seq or since < record.forgotten_seq:
                return None
            changed: set[int] = set()
            removed: set[int] = set()
            for change in record.changes:
                if change.seq > since:
                    changed |= change.changed
                    removed |= change.removed
            return changed - removed, removed

    def cursor(self, seq: int) -> str:
        return f"{self.epoch}:{seq}"

    def parse(self, cursor: Optional[str]) -> Optional[int]:
        """The number a cursor of this run stands for; None for no cursor or another run's."""
        if not cursor:
            return None
        epoch, _, seq = cursor.partition(":")
        if epoch != self.epoch or not seq.isdigit():
            return None
        return int(seq)
