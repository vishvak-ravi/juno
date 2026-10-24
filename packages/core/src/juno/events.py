"""Event types and ids.

The record holds events of the types the system owns (``EVENT_TYPES``): an
``activity`` event is one row of a recorded session, a screenshot and the inputs
that followed it, captioned by chunk (``server.routes.events``); a ``message``
event is one turn of a conversation with an assistant
(``juno.conversations``). The lattice is built over the types its build names
(``lattice.build``).

Row ``n`` (from 0) of a session is event ``<session id>-<n as six digits>``. A
recorder uploads each row as ``PUT /events/<event id>`` (``capture.upload``); the
server lists and serves events by the same id.
"""

from __future__ import annotations

import re
from typing import Literal

from juno.sessions import SESSION_ID_PATTERN

ACTIVITY = "activity"
MESSAGE = "message"
EVENT_TYPES = (ACTIVITY, MESSAGE)
EventType = Literal["activity", "message"]

ROW_DIGITS = 6
EVENT_ID = re.compile(rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<row>\d{{{ROW_DIGITS},}})$")


def event_id_of(session_id: str, row: int) -> str:
    """The id of row ``row`` of the session with id ``session_id``."""
    return f"{session_id}-{row:0{ROW_DIGITS}d}"
