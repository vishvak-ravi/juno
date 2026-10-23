"""The store report: what arrived, what is annotated, and what is broken on disk."""

import json

from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Annotation
from juno.sessions import session_dir
from annotate.store import caption_file, new_caption, write_caption_file
from juno.config import AnnotateConfig, ServerSettings, devices_dir
from server.app import create_app
from server.integrity import check_store

SESSION = "laptop-session_20260101_120000"


def write_session(tmp_path, session_id: str, rows: int):
    """A stored session of ``rows`` aggregations, each with its screenshot."""
    directory = session_dir(devices_dir(tmp_path), session_id)
    directory.mkdir(parents=True)
    lines = []
    for index in range(rows):
        path = directory / f"{index}.png"
        Image.new("RGB", (4, 4), "white").save(path)
        lines.append(json.dumps({
            "timestamp": 1000.0 + index, "reason": "key_start", "event_type": "key",
            "request_state": "start", "screenshot_path": str(path),
            "screenshot_timestamp": 1000.0 + index, "end_screenshot_timestamp": 1001.0 + index,
            "num_events": 0, "events": [], "cursor_position": None,
            "monitor": None, "burst_id": 1, "scale_factor": 1.0}))
    (directory / "event_rows.jsonl").write_text("\n".join(lines) + "\n")
    return directory


def test_a_sound_store_reports_per_device_what_arrived_and_what_is_annotated(tmp_path):
    directory = write_session(tmp_path, SESSION, rows=7)
    write_session(tmp_path, "desktop-session_20260101_120000", rows=2)
    write_caption_file(directory, new_caption(SESSION, 0, "any"))  # chunk 0 pending; 1, 2 not asked for

    report = check_store(devices_dir(tmp_path), chunk_size=2)

    assert report.issues == []
    assert [(d.id, d.sessions, d.rows, d.chunks, d.pending, d.not_requested)
            for d in report.devices] == [("desktop", 1, 2, 1, 0, 1), ("laptop", 1, 7, 3, 1, 2)]
    laptop = next(s for s in report.sessions if s.id == SESSION)
    assert (laptop.screenshots_expected, laptop.screenshots_stored) == (7, 7)
    assert laptop.rows_after_last_chunk == 1
    assert (laptop.start_time, laptop.end_time) == (1000.0, 1006.0)


def test_each_broken_thing_on_disk_is_reported_as_an_issue(tmp_path):
    directory = write_session(tmp_path, SESSION, rows=4)
    (directory / "1.png").unlink()
    with open(directory / "event_rows.jsonl", "a") as rows:
        rows.write("not a row\n")
    done = new_caption(SESSION, 0, "any").model_copy(update={"status": "done"})
    write_caption_file(directory, done)  # done, but nothing came back
    write_caption_file(directory, new_caption(SESSION, 9, "any"))  # the session has no such rows
    stray = new_caption("desktop-session_20260101_120000", 1, "any")
    caption_file(directory, 1).write_text(stray.model_dump_json())  # another session's record
    caption_file(directory, 2).write_text("{")

    report = check_store(devices_dir(tmp_path), chunk_size=2)

    assert sorted((issue.kind, issue.subject) for issue in report.issues) == [
        ("caption_incomplete", "0"), ("caption_misfiled", "0001.json"),
        ("caption_unreadable", "0002.json"), ("caption_without_rows", "9"),
        ("row_unreadable", "4"), ("screenshot_missing", "1")]
    assert report.devices[0].issues == 6


def test_the_report_is_served_and_the_server_starts_on_a_broken_store(tmp_path):
    directory = write_session(tmp_path, SESSION, rows=2)
    (directory / "0.png").unlink()
    config = ServerSettings(annotate=AnnotateConfig(chunk_size=2))
    app = create_app(config, data_dir=tmp_path, annotate_request=lambda request: Annotation(
        actions=[], dense_caption="", sanitization=request.sanitization))

    with TestClient(app) as client:
        report = client.get("/api/integrity").json()

    assert [issue["kind"] for issue in report["issues"]] == ["screenshot_missing"]
    assert report["sessions"][0]["screenshots_stored"] == 1
