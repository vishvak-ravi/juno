"""A recorder's session reaching the server: the upload route, and the uploader that calls it."""

import io
import json
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Action, Annotation
from capture.upload import AggregationUploader, copy_sessions
from juno.config import AnnotateConfig, CaptureConfig, Config
from server.app import create_app

SESSION = "laptop-session_20260101_120000"
MONITOR = {"left": 0, "top": 0, "width": 40, "height": 20}


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def row(index: int, screenshot_path: str) -> str:
    """One aggregation as a recorder writes it, pointing at a file on the recorder's disk."""
    shot = 1000.0 + index
    event = {"timestamp": shot + 0.5, "monitor_index": 0, "monitor": MONITOR,
             "event_type": "key_press", "details": {"key": "a"}, "cursor_position": [1, 1]}
    return json.dumps({
        "timestamp": shot, "reason": "key_start", "event_type": "key",
        "request_state": "start", "screenshot_path": screenshot_path,
        "screenshot_timestamp": shot, "end_screenshot_timestamp": shot + 1.0,
        "num_events": 1, "events": [event], "cursor_position": [1, 1],
        "monitor": MONITOR, "burst_id": 1, "scale_factor": 1.0})


def annotate_request(request):
    return Annotation(actions=[Action(start=1, end=2, caption="Typed a.")],
                      dense_caption="a", sanitization=request.sanitization)


def app_for(tmp_path):
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2))
    return create_app(config, annotate_request=annotate_request)


def put(client, session, index):
    return client.put(f"/events/{session}-{index:06d}",
                      data={"aggregation": row(index, f"/recorder/disk/{index}.png")},
                      files={"screenshot": (f"{index}.png", png(), "image/png")})


def wait_for(condition, what):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} did not happen")


def test_uploaded_rows_are_stored_with_their_screenshots_and_a_full_chunk_is_annotated(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        assert put(client, SESSION, 0).status_code == 201
        # Nothing is annotated until the chunk's last row is there.
        assert client.get(f"/annotations/{SESSION}-0000").status_code == 404
        assert put(client, SESSION, 1).status_code == 201

        screenshot = client.get(f"/events/{SESSION}-000001/screenshot")
        assert screenshot.status_code == 200
        assert screenshot.content == png()
        session = client.get(f"/sessions/{SESSION}").json()
        assert (session["device_id"], session["aggregations"]) == ("laptop", 2)

        wait_for(lambda: client.get(f"/annotations/{SESSION}-0000").json().get("status") == "done",
                 "the annotation of the full chunk")
        annotation = client.get(f"/annotations/{SESSION}-0000").json()
        assert annotation["device_id"] == "laptop"
        assert len(annotation["frames"]) == 2


def test_a_repeated_row_is_not_stored_twice_and_a_row_out_of_order_is_refused(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        assert put(client, SESSION, 0).status_code == 201
        assert put(client, SESSION, 0).status_code == 200
        assert client.get(f"/sessions/{SESSION}").json()["aggregations"] == 1

        skipped = put(client, SESSION, 2)
        assert skipped.status_code == 409
        assert skipped.json()["detail"]["expected_row"] == 1


def test_each_device_has_its_own_sessions_even_with_the_same_session_name(tmp_path):
    other = "desktop-session_20260101_120000"
    with TestClient(app_for(tmp_path)) as client:
        for index in range(2):
            assert put(client, SESSION, index).status_code == 201
        assert put(client, other, 0).status_code == 201

        both = client.get("/sessions", params={"order": "asc"}).json()["data"]
        assert [(s["id"], s["aggregations"]) for s in both] == [(other, 1), (SESSION, 2)]
        only = client.get("/aggregations", params={"device": "desktop"}).json()["data"]
        assert [item["id"] for item in only] == [f"{other}-000000"]
        wait_for(lambda: client.get(f"/annotations/{SESSION}-0000").json().get("status") == "done",
                 "the annotation of the full chunk")
        assert client.get("/annotations", params={"device": "desktop"}).json()["data"] == []


def running_server(tmp_path):
    """The app served over HTTP on a free port: (server, its thread, the port)."""
    server = uvicorn.Server(uvicorn.Config(app_for(tmp_path / "server"), host="127.0.0.1",
                                           port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, "the server starting")
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


def write_recorded(directory, rows: int):
    """A session on the recorder's disk with ``rows`` aggregations and their screenshots."""
    directory.mkdir(parents=True)
    with open(directory / "raw_aggregations.jsonl", "a") as file:
        for index in range(rows):
            shot = directory / f"{index}.png"
            shot.write_bytes(png())
            file.write(row(index, str(shot)) + "\n")


def test_sessions_already_recorded_are_copied_unannotated_and_a_second_copy_adds_nothing(tmp_path):
    server, thread, port = running_server(tmp_path)
    recorded = tmp_path / "recorder"
    write_recorded(recorded / "session_20260101_120000", rows=3)
    write_recorded(recorded / "session_20260102_120000", rows=2)
    url = f"http://127.0.0.1:{port}/events"
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            # The server already has the first row of one session.
            assert put(client, SESSION, 0).status_code == 201

            assert copy_sessions(recorded, url, "laptop")
            assert copy_sessions(recorded, url, "laptop")

            sessions = client.get("/sessions", params={"order": "asc"}).json()["data"]
            assert [(s["id"], s["aggregations"], s["annotations"]) for s in sessions] == [
                (SESSION, 3, 0), ("laptop-session_20260102_120000", 2, 0)]
            # A copied chunk is annotated when asked for.
            assert client.put(f"/annotations/{SESSION}-0000").status_code == 202
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def test_the_uploader_sends_every_complete_row_in_order_with_its_screenshot(tmp_path):
    server, thread, port = running_server(tmp_path)

    recorded = tmp_path / "recorder" / "session_20260101_120000"
    recorded.mkdir(parents=True)
    uploader = AggregationUploader(recorded, f"http://127.0.0.1:{port}/events", "laptop")
    try:
        uploader.on_aggregation()  # no file yet
        with open(recorded / "raw_aggregations.jsonl", "a") as file:
            for index in range(3):
                shot = recorded / f"{index}.png"
                shot.write_bytes(png())
                file.write(row(index, str(shot)) + "\n")
                file.flush()
                uploader.on_aggregation()
            file.write('{"half a row')  # no newline: not sent
            file.flush()
        uploader.close()

        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            stored = client.get("/aggregations", params={"session_id": SESSION,
                                                         "order": "asc"}).json()["data"]
            assert [item["id"] for item in stored] == [f"{SESSION}-{n:06d}" for n in range(3)]
            # The stored rows point at the server's copies, not the recorder's files.
            assert all(str(tmp_path / "server") in item["screenshot_path"] for item in stored)
            assert client.get(f"/events/{SESSION}-000002/screenshot").content == png()
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
