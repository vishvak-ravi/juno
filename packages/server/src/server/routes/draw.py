"""``/draw``: a drawing page for a tablet, recorded like any other device.

``GET /draw`` is the page (``apps/web/draw.html``). It opens
``/draw/socket`` and sends, in order:

1. ``{"width": W, "height": H}``: its canvas size in pixels, once;
2. pointer events as ``{"type": "down" | "up" | "move", "x": X, "y": Y}`` in
   canvas pixels, and the canvas as a PNG (a binary message) whenever it changed.

The server answers the first message with ``{"fps": N}``, how often a frame is
worth sending, and records the page with ``server.draw.DrawRecorder`` until the
socket closes. Events are timed when they arrive, on the same clock as the
recorder's frames.
"""

import asyncio
import io
import logging
from typing import Literal

import numpy as np
from fastapi import APIRouter, WebSocket
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from capture.sources import pushed
from server.draw import DrawRecorder
from server.routes.web import WEB_DIR

logger = logging.getLogger(__name__)

router = APIRouter(tags=["draw"])

DRAW_PAGE = WEB_DIR / "draw.html"


class Canvas(BaseModel):
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)


class Pointer(BaseModel):
    type: Literal["down", "up", "move"]
    x: int
    y: int


def decode_frame(png: bytes) -> np.ndarray:
    """The PNG as an H x W x 3 RGB array."""
    with Image.open(io.BytesIO(png)) as image:
        return np.asarray(image.convert("RGB"))


@router.get("/draw", include_in_schema=False)
def get_draw_page() -> FileResponse:
    # no-cache: a copy saved to the Home Screen asks for the current page each time.
    return FileResponse(DRAW_PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})


@router.websocket("/draw/socket")
async def draw_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    state = websocket.app.state
    try:
        canvas = Canvas.model_validate(await websocket.receive_json())
    except ValidationError as exc:
        await websocket.close(code=1003, reason=str(exc)[:120])
        return
    backend = pushed.create(canvas.width, canvas.height)
    # The address this socket reached the server at, which its uploads can reach too.
    host, port = websocket.scope["server"][:2]
    recorder = DrawRecorder(backend, state.capture, state.control_config.poll_seconds,
                            state.control, state.draw_sessions_dir,
                            f"http://{host}:{port}/events", state.draw.device_id)
    recorder.start()
    try:
        await websocket.send_json({"fps": state.capture.fps})
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                frame = await asyncio.to_thread(decode_frame, message["bytes"])
                if frame.shape[:2] != (canvas.height, canvas.width):
                    logger.warning("dropped a %dx%d frame from a %dx%d canvas",
                                   frame.shape[1], frame.shape[0], canvas.width, canvas.height)
                    continue
                backend.screen.push(frame)
            elif message.get("text") is not None:
                pointer = Pointer.model_validate_json(message["text"])
                if pointer.type == "move":
                    backend.input.move((pointer.x, pointer.y))
                else:
                    backend.input.click(pointer.type == "down", (pointer.x, pointer.y))
    finally:
        await asyncio.to_thread(recorder.close)
