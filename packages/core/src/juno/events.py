"""Event ids: row ``n`` (from 0) of a session is event ``<session id>-<n as six digits>``.

A recorder uploads each row as ``PUT /events/<event id>`` (``capture.upload``);
the server lists and serves events by the same id (``server.routes.events``).
"""

from __future__ import annotations

import re

from juno.sessions import SESSION_ID_PATTERN

ROW_DIGITS = 6
EVENT_ID = re.compile(rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<row>\d{{{ROW_DIGITS},}})$")


def event_id_of(session_id: str, row: int) -> str:
    """The id of row ``row`` of the session with id ``session_id``."""
    return f"{session_id}-{row:0{ROW_DIGITS}d}"
