"""The switches clients set and what each recorder last reported, in one JSON file.

``<data_dir>/control.json`` (``juno.config.control_file``) holds a ``server``
section, whose ``annotate`` switch pauses or resumes the annotation queue, and a
``devices`` section with each device's ``capture`` switch and last report. Only
the server reads and writes the file; other devices go through ``/control``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field


class ServerSwitches(BaseModel):
    # Whether the annotation queue makes model calls.
    annotate: bool = True


class Report(BaseModel):
    """What a recorder is doing, as it reports it."""

    capture: Literal["running", "paused"]
    # The session being recorded, while running.
    session_id: Optional[str] = None


class DeviceState(BaseModel):
    # Whether the device's recorder should record.
    capture: bool = True
    reported: Optional[Report] = None
    # Unix seconds of the last report.
    reported_at: Optional[float] = None


class ControlState(BaseModel):
    server: ServerSwitches = Field(default_factory=ServerSwitches)
    devices: dict[str, DeviceState] = {}


# A change reads, edits and replaces the file; two at once must not interleave.
lock = threading.Lock()


def read_state(path: Path) -> ControlState:
    try:
        return ControlState.model_validate_json(path.read_text())
    except FileNotFoundError:
        return ControlState()


def change_state(path: Path, change: Callable[[ControlState], None]) -> ControlState:
    """Apply ``change`` to the stored state and store the result in one step."""
    with lock:
        state = read_state(path)
        change(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(indent=2))
        os.replace(temporary, path)
        return state


def record_report(path: Path, device_id: str, report: Report) -> bool:
    """Store a recorder's report, adding the device if new; returns whether it should record."""
    def change(state: ControlState) -> None:
        device = state.devices.setdefault(device_id, DeviceState())
        device.reported = report
        device.reported_at = time.time()
    return change_state(path, change).devices[device_id].capture
