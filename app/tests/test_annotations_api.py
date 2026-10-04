"""The annotation routes, with the model call replaced by a function."""

import json
import time

from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Action, Annotation
from juno.config import AnnotateConfig, CaptureConfig, Config, sessions_dir
from server.app import create_app

SESSION = "session_20260101_120000"
MONITOR = {"left": 0, "top": 0, "width": 40, "height": 20}


def write_session(tmp_path, rows: int):
    """A session of ``rows`` aggregations, one key press each, a second apart."""
    session = sessions_dir(tmp_path) / SESSION
    session.mkdir(parents=True)
    lines = []
    for index in range(rows):
        shot = 1000.0 + index
        path = session / f"{index}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        event = {"timestamp": shot + 0.5, "monitor_index": 0, "monitor": MONITOR,
                 "event_type": "key_press", "details": {"key": "a"}, "cursor_position": [1, 1]}
        lines.append(json.dumps({
            "timestamp": shot, "reason": "key_start", "event_type": "key",
            "request_state": "start", "screenshot_path": str(path),
            "screenshot_timestamp": shot, "end_screenshot_timestamp": shot + 1.0,
            "num_events": 1, "events": [event], "cursor_position": [1, 1],
            "monitor": MONITOR, "burst_id": 1, "scale_factor": 1.0}))
    (session / "raw_aggregations.jsonl").write_text("\n".join(lines) + "\n")


def client_for(tmp_path, annotate_request) -> TestClient:
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2))
    return TestClient(create_app(config, annotate_request=annotate_request))


def wait_until_settled(client, annotation_id):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        record = client.get(f"/annotations/{annotation_id}").json()
        if record["status"] != "pending":
            return record
        time.sleep(0.02)
    raise AssertionError(f"{annotation_id} stayed pending")


def test_a_put_annotates_the_chunk_once_and_the_result_can_be_read_back(tmp_path):
    write_session(tmp_path, rows=5)
    sent = []

    def annotate_request(request):
        sent.append(request)
        return Annotation(actions=[Action(start=1, end=2, caption="Typed a.")],
                          dense_caption="a", sanitization=request.sanitization)

    client = client_for(tmp_path, annotate_request)
    first, second = f"{SESSION}-0000", f"{SESSION}-0001"

    response = client.put(f"/annotations/{second}")
    assert response.status_code == 202
    assert response.headers["Location"] == f"/annotations/{second}"
    assert response.json()["status"] == "pending"

    record = wait_until_settled(client, second)
    assert record["status"] == "done"
    assert record["annotation"]["actions"][0]["caption"] == "Typed a."
    assert record["annotation"]["sanitization"]["algorithm"] == "typed-key-regex"
    assert (record["start_time"], record["end_time"]) == (1002.0, 1003.0)
    assert [frame["label"] for frame in record["frames"]] == ["Frame 1", "Frame 2"]
    # Rows 2 and 3 of the session, and nothing sent unsanitized.
    assert [frame.timestamp for frame in sent[0].frames] == [1002.0, 1003.0]
    assert sent[0].sanitization is not None

    # Repeating the PUT returns the stored annotation without another model call.
    again = client.put(f"/annotations/{second}")
    assert again.status_code == 200 and again.json() == record
    assert len(sent) == 1

    client.put(f"/annotations/{first}")
    wait_until_settled(client, first)
    listed = client.get("/annotations", params={"order": "asc"}).json()
    assert [item["id"] for item in listed["data"]] == [first, second]
    in_range = client.get("/annotations", params={"since": 1002.5}).json()
    assert [item["id"] for item in in_range["data"]] == [second]

    assert client.put(f"/annotations/{SESSION}-0003").status_code == 404  # only 5 rows
    assert client.put("/annotations/no_such_session-0000").status_code == 404
    assert client.get(f"/annotations/{SESSION}-0002").status_code == 404  # never requested


def test_a_failed_model_call_is_recorded_and_a_new_put_retries_it(tmp_path):
    write_session(tmp_path, rows=2)
    outcomes = [RuntimeError("rate limited"), None]

    def annotate_request(request):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return Annotation(actions=[], dense_caption="", sanitization=request.sanitization)

    client = client_for(tmp_path, annotate_request)
    annotation_id = f"{SESSION}-0000"

    client.put(f"/annotations/{annotation_id}")
    failed = wait_until_settled(client, annotation_id)
    assert failed["status"] == "failed" and failed["error"] == "RuntimeError: rate limited"

    assert client.put(f"/annotations/{annotation_id}").status_code == 202
    assert wait_until_settled(client, annotation_id)["status"] == "done"


def test_sessions_and_their_aggregations_can_be_browsed(tmp_path):
    write_session(tmp_path, rows=3)
    client = client_for(tmp_path, annotate_request=None)

    (session,) = client.get("/sessions").json()["data"]
    assert (session["id"], session["aggregations"], session["annotations"]) == (SESSION, 3, 0)
    assert client.get(f"/sessions/{SESSION}").json() == session
    assert client.get("/sessions/no_such_session").status_code == 404

    page = client.get("/aggregations", params={"session_id": SESSION, "order": "asc",
                                               "since": 1001.0}).json()
    assert [item["id"] for item in page["data"]] == [f"{SESSION}-000001", f"{SESSION}-000002"]
    assert page["data"][0]["events"][0]["details"] == {"key": "a"}

    screenshot = client.get(f"/aggregations/{SESSION}-000001/screenshot")
    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert client.get(f"/aggregations/{SESSION}-000003").status_code == 404
