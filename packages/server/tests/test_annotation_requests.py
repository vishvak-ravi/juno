"""Annotating a stored chunk through the server's worker, with the model call replaced by a function."""

import json
import time

from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Action, Annotation
from juno.sessions import session_dir
from annotate.store import list_records, new_record, read_record, write_record
from juno.config import AnnotateConfig, CaptureConfig, Config, LlmConfig, devices_dir
from server.app import create_app

SESSION = "laptop-session_20260101_120000"
MONITOR = {"left": 0, "top": 0, "width": 40, "height": 20}


def write_session(tmp_path, rows: int):
    """A session of ``rows`` aggregations, one key press each, a second apart."""
    session = session_dir(devices_dir(tmp_path), SESSION)
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


def wait_until_settled(tmp_path, chunk):
    """The chunk's stored annotation once it is no longer pending."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        record = read_record(session_dir(devices_dir(tmp_path), SESSION), chunk)
        if record is not None and record.status != "pending":
            return record
        time.sleep(0.02)
    raise AssertionError(f"chunk {chunk} stayed pending")


def test_a_request_annotates_the_chunk_once_and_the_result_is_stored(tmp_path):
    write_session(tmp_path, rows=5)
    sent = []

    def annotate_request(request):
        sent.append(request)
        return Annotation(actions=[Action(start=1, end=2, caption="Typed a.")],
                          dense_caption="a", sanitization=request.sanitization)

    worker = client_for(tmp_path, annotate_request).app.state.worker

    assert worker.request(SESSION, 1).status == "pending"
    record = wait_until_settled(tmp_path, 1)
    assert record.status == "done"
    assert record.annotation.actions[0].caption == "Typed a."
    assert record.annotation.sanitization.algorithm == "typed-key-regex"
    assert (record.start_time, record.end_time) == (1002.0, 1003.0)
    assert [frame.label for frame in record.frames] == ["Frame 1", "Frame 2"]
    # Rows 2 and 3 of the session, and nothing sent unsanitized.
    assert [frame.timestamp for frame in sent[0].frames] == [1002.0, 1003.0]
    assert sent[0].sanitization is not None

    # Asking again returns the stored annotation without another model call.
    assert worker.request(SESSION, 1) == record
    assert len(sent) == 1

    worker.request(SESSION, 0)
    wait_until_settled(tmp_path, 0)
    stored = devices_dir(tmp_path)
    assert [r.chunk for r in list_records(stored)] == [0, 1]
    assert [r.chunk for r in list_records(stored, since=1002.5)] == [1]


def test_a_failed_model_call_is_recorded_and_a_new_request_retries_it(tmp_path):
    write_session(tmp_path, rows=2)
    outcomes = [RuntimeError("rate limited"), None]

    def annotate_request(request):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return Annotation(actions=[], dense_caption="", sanitization=request.sanitization)

    worker = client_for(tmp_path, annotate_request).app.state.worker

    worker.request(SESSION, 0)
    failed = wait_until_settled(tmp_path, 0)
    assert failed.status == "failed" and failed.error == "RuntimeError: rate limited"

    assert worker.request(SESSION, 0).status == "pending"
    assert wait_until_settled(tmp_path, 0).status == "done"


def test_an_agent_reads_finished_annotations_over_mcp(tmp_path):
    write_session(tmp_path, rows=2)

    def annotate_request(request):
        return Annotation(actions=[Action(start=1, end=2, caption="Typed a.")],
                          dense_caption="a", sanitization=request.sanitization)

    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2))
    app = create_app(config, annotate_request=annotate_request)
    annotation_id = f"{SESSION}-0000"

    def call(client, tool, arguments):
        response = client.post(
            "/mcp", headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": tool, "arguments": arguments}})
        result = response.json()["result"]
        return result["isError"], json.loads(result["content"][0]["text"]) \
            if not result["isError"] else result["content"][0]["text"]

    # The MCP endpoint only answers requests addressed to the server's own host.
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.app.state.worker.request(SESSION, 0)
        wait_until_settled(tmp_path, 0)

        is_error, listed = call(client, "list_annotations", {"limit": 5})
        assert not is_error
        assert [(a["id"], a["actions"], a["text_on_screen"]) for a in listed["annotations"]] == [
            (annotation_id, ["Typed a."], "a")]

        assert listed["devices"] == ["laptop"]
        assert listed["annotations"][0]["device"] == "laptop"
        is_error, only = call(client, "list_annotations", {"device": "laptop"})
        assert not is_error and [a["id"] for a in only["annotations"]] == [annotation_id]
        is_error, message = call(client, "list_annotations", {"device": "phone"})
        assert is_error and "laptop" in message

        is_error, full = call(client, "get_annotation", {"annotation_id": annotation_id})
        assert not is_error
        assert full["actions"] == [{"start": 1, "end": 2, "caption": "Typed a."}]
        assert "Key pressed: a" in full["frames"][0]["input"]

        is_error, message = call(client, "get_annotation", {"annotation_id": "nope"})
        assert is_error and "list_annotations" in message


def test_a_listing_too_long_for_the_agent_keeps_the_newest_and_says_more_is_available(tmp_path):
    write_session(tmp_path, rows=6)

    def annotate_request(request):
        return Annotation(actions=[Action(start=1, end=2, caption="Typed a.")],
                          dense_caption="a" * 400, sanitization=request.sanitization)

    budget = 1500  # room for two of the three annotations
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2),
                    llm=LlmConfig(tool_result_chars=budget))
    with TestClient(create_app(config, annotate_request=annotate_request),
                    base_url="http://127.0.0.1:8765") as client:
        for chunk in range(3):
            client.app.state.worker.request(SESSION, chunk)
            wait_until_settled(tmp_path, chunk)
        response = client.post(
            "/mcp", headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "list_annotations", "arguments": {}}})
        text = response.json()["result"]["content"][0]["text"]

    assert len(text) <= budget
    listed = json.loads(text)
    assert [a["id"] for a in listed["annotations"]] == [f"{SESSION}-0002", f"{SESSION}-0001"]
    assert listed["more_available"] is True


def test_an_annotation_left_pending_by_a_stopped_server_is_finished_by_the_next(tmp_path):
    write_session(tmp_path, rows=2)
    write_record(session_dir(devices_dir(tmp_path), SESSION),
                 new_record(SESSION, 0, "gemini/any"))  # queued, then the server stopped

    def annotate_request(request):
        return Annotation(actions=[], dense_caption="", sanitization=request.sanitization)

    with client_for(tmp_path, annotate_request) as client:  # startup runs
        assert wait_until_settled(tmp_path, 0).status == "done"
