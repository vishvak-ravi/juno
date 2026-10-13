"""The tablet drawing page: what it draws reaches /events like any recorder's session."""

import io
import json
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image
from websockets.sync.client import connect

from juno.config import AnnotateConfig, CaptureConfig, Config, ControlConfig, DrawConfig
from server.app import create_app
from test_upload import annotate_request, wait_for

WIDTH, HEIGHT = 64, 48


def frame(colour) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def serve(tmp_path):
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2),
                    control=ControlConfig(poll_seconds=0.2),
                    draw=DrawConfig(device_id="tablet"))
    server = uvicorn.Server(uvicorn.Config(create_app(config, annotate_request=annotate_request),
                                           host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, "the server starting")
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


def draw_strokes(port, strokes: int) -> None:
    with connect(f"ws://127.0.0.1:{port}/draw/socket") as socket:
        socket.send(json.dumps({"width": WIDTH, "height": HEIGHT}))
        assert json.loads(socket.recv())["fps"] > 0
        socket.send(frame("black"))
        time.sleep(0.6)
        for index in range(strokes):
            socket.send(json.dumps({"type": "down", "x": 10 + index, "y": 10}))
            socket.send(json.dumps({"type": "move", "x": 20 + index, "y": 30}))
            socket.send(json.dumps({"type": "up", "x": 20 + index, "y": 30}))
            socket.send(frame((255, 255, 255 - index)))
            time.sleep(1.0)


def test_strokes_drawn_on_the_page_are_stored_as_the_tablet_s_events(tmp_path):
    server, thread, port = serve(tmp_path)
    try:
        draw_strokes(port, strokes=2)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            def stored():
                return client.get("/events", params={"device": "tablet", "order": "asc",
                                                     "limit": 100}).json()["data"]
            wait_for(lambda: any(i["type"] == "mouse_up" for e in stored() for i in e["inputs"]),
                     "the strokes reaching /events")
            events = stored()
            inputs = [i for event in events for i in event["inputs"]]
            assert [(i["type"], i["x"], i["y"]) for i in inputs if i["type"] != "mouse_move"] == [
                ("mouse_down", 10, 10), ("mouse_up", 20, 30),
                ("mouse_down", 11, 10), ("mouse_up", 21, 30)]
            assert all(i["button"] == "Button.left" for i in inputs if i["type"] != "mouse_move")
            assert all(e["id"].startswith("tablet-session_") for e in events)
            shot = client.get(events[0]["screenshot_url"])
            assert Image.open(io.BytesIO(shot.content)).size == (WIDTH, HEIGHT)

            # The page's device has a switch like any recorder, and is paused once it closes.
            wait_for(lambda: client.get("/control").json()["devices"]["tablet"]["reported"]
                     ["capture"] == "paused", "the closed page reporting paused")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_nothing_is_recorded_while_the_tablet_s_switch_is_off(tmp_path):
    server, thread, port = serve(tmp_path)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            client.put("/control/devices/tablet/report", json={"capture": "paused"})
            client.patch("/control/devices/tablet", json={"capture": False})
            draw_strokes(port, strokes=1)
            time.sleep(0.5)
            assert client.get("/events", params={"device": "tablet"}).json()["data"] == []
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_the_server_serves_the_drawing_page(tmp_path):
    with TestClient(create_app(Config(capture=CaptureConfig(data_dir=tmp_path)))) as client:
        response = client.get("/draw")
        assert response.status_code == 200
        assert "<canvas" in response.text
