"""The switches clients set and what each recorder last reported.

The state has a ``server`` section, whose ``annotate`` switch pauses or resumes
the captioning and question queues, and a ``devices`` section with each
device's ``capture`` switch and last report. Only the server reads and writes
it; other devices go through ``/control``. ``Control`` is what the server reads
and writes through; ``FileControl`` keeps the state in one JSON file,
``<data_dir>/control.json`` (``juno.config.control_file``).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from pydantic import BaseModel, Field

from juno.control import Report
from juno.sessions import replace_file


class ServerSwitches(BaseModel):
    # Whether the annotation queue makes model calls.
    annotate: bool = True


class DeviceState(BaseModel):
    # Whether the device's recorder should record.
    capture: bool = True
    reported: Optional[Report] = None
    # Unix seconds of the last report.
    reported_at: Optional[float] = None


class ControlState(BaseModel):
    server: ServerSwitches = Field(default_factory=ServerSwitches)
    devices: dict[str, DeviceState] = {}


class UnknownDevice(KeyError):
    """No device with this id has reported."""


class Control(Protocol):

    def read(self) -> ControlState: ...

    def record_report(self, device_id: str, report: Report) -> bool:
        """Store a recorder's report, adding the device if new; returns whether it should record."""

    def set_recording(self, device_id: str, on: bool) -> ControlState:
        """Turn a device's recording on or off; ``UnknownDevice`` if it never reported."""

    def set_annotating(self, on: bool) -> ControlState:
        """Turn the server's captioning and question queues on or off."""


class FileControl:

    def __init__(self, path: Path):
        self.path = path
        # A change reads, edits and replaces the file; two at once must not interleave.
        self._lock = threading.Lock()

    def read(self) -> ControlState:
        try:
            return ControlState.model_validate_json(self.path.read_text())
        except FileNotFoundError:
            return ControlState()

    def record_report(self, device_id: str, report: Report) -> bool:
        def change(state: ControlState) -> None:
            device = state.devices.setdefault(device_id, DeviceState())
            device.reported = report
            device.reported_at = time.time()
        return self._change(change).devices[device_id].capture

    def set_recording(self, device_id: str, on: bool) -> ControlState:
        def change(state: ControlState) -> None:
            if device_id not in state.devices:
                raise UnknownDevice(device_id)
            state.devices[device_id].capture = on
        return self._change(change)

    def set_annotating(self, on: bool) -> ControlState:
        def change(state: ControlState) -> None:
            state.server.annotate = on
        return self._change(change)

    def _change(self, change: Callable[[ControlState], None]) -> ControlState:
        with self._lock:
            state = self.read()
            change(state)
            replace_file(self.path, state.model_dump_json(indent=2))
            return state
