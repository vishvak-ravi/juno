"""What a recorder tells the server and what it is told back.

A recorder sends a ``Report`` as ``PUT /control/devices/{id}/report``
(``capture.__main__``) and the server answers with an ``Instruction``
(``server.routes.control``).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Report(BaseModel):
    """What a recorder is doing, as it reports it."""

    capture: Literal["running", "paused"]
    # The session being recorded, while running.
    session_id: Optional[str] = None


class Instruction(BaseModel):
    """The answer to a report: whether the recorder should record."""

    capture: bool
