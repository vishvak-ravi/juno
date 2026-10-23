"""``/control``: the switches of the server and of each device, and what each device reports.

``GET /control`` is everything the status page shows. ``PATCH /control/server``
pauses or resumes the annotation queue; ``PATCH /control/devices/{id}`` turns a
device's recording on or off. A recorder sends ``PUT /control/devices/{id}/report``
and is answered with whether to record and, from ``control.poll_seconds``, when to
report again; its first report adds the device, recording.
"""

import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from juno.control import Instruction, Report
from juno.sessions import DEVICE_ID_PATTERN
from server.control import ControlState, DeviceState, UnknownDevice
from server.worker import QueueStatus

router = APIRouter(prefix="/control", tags=["control"])

DeviceId = Annotated[str, PathParam(pattern=f"^{DEVICE_ID_PATTERN}$", max_length=64)]


class ServerView(BaseModel):
    annotate: bool
    queue: QueueStatus


class DeviceView(DeviceState):
    # Whether the device reported within the last ``control.offline_seconds``.
    online: bool


class ControlView(BaseModel):
    server: ServerView
    devices: dict[str, DeviceView]
    # Seconds between a recorder's reports; how often this is worth asking for again.
    poll_seconds: float
    # Seconds between a client's checks of GET /changes.
    live_seconds: float


class ServerPatch(BaseModel):
    annotate: bool


class DevicePatch(BaseModel):
    capture: bool


def view(request: Request, state: ControlState) -> ControlView:
    app = request.app.state
    now = time.time()

    def device_view(device: DeviceState) -> DeviceView:
        online = (device.reported_at is not None
                  and now - device.reported_at < app.control_config.offline_seconds)
        return DeviceView(**device.model_dump(), online=online)

    return ControlView(
        server=ServerView(annotate=state.server.annotate, queue=app.worker.status()),
        devices={id: device_view(device) for id, device in sorted(state.devices.items())},
        poll_seconds=app.control_config.poll_seconds,
        live_seconds=app.control_config.live_seconds)


@router.get("")
def get_control(request: Request) -> ControlView:
    return view(request, request.app.state.control.read())


@router.patch("/server")
def patch_server(body: ServerPatch, request: Request) -> ControlView:
    state = request.app.state.control.set_annotating(body.annotate)
    request.app.state.worker.set_annotating(state.server.annotate)
    if request.app.state.question_worker is not None:
        request.app.state.question_worker.set_asking(state.server.annotate)
    return view(request, state)


@router.patch("/devices/{device_id}")
def patch_device(device_id: DeviceId, body: DevicePatch, request: Request) -> ControlView:
    try:
        state = request.app.state.control.set_recording(device_id, body.capture)
    except UnknownDevice:
        raise HTTPException(status_code=404, detail=f"no device {device_id} has reported")
    return view(request, state)


@router.put("/devices/{device_id}/report")
def report(device_id: DeviceId, body: Report, request: Request) -> Instruction:
    """Store the recorder's report; the answer says whether it should record and when to report next."""
    return Instruction(capture=request.app.state.control.record_report(device_id, body),
                       poll_seconds=request.app.state.control_config.poll_seconds)
