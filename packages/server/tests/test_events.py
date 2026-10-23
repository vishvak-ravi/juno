"""The events API: every device's rows as one list by time, with captions, edits and deletes."""

import io
import json
import shutil
import time

from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Action, Annotation
from annotate.store import FileActivityCaptions
from juno.config import AnnotateConfig, CaptureConfig, Config, devices_dir
from server.app import create_app
from server.events import FileEvents

LAPTOP = "laptop-session_20260101_120000"
DESKTOP = "desktop-session_20260101_120000"
MONITOR = {"left": 0, "top": 0, "width": 40, "height": 20}


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def row(at: float, screenshot: bool = True) -> str:
    click = {"timestamp": at + 0.5, "monitor_index": 0, "monitor": MONITOR,
             "event_type": "mouse_down", "details": {"x": 3, "y": 4, "button": "left"},
             "cursor_position": [3, 4]}
    return json.dumps({
        "timestamp": at, "reason": "click_start_stale", "event_type": "click",
        "request_state": "start", "screenshot_path": f"/recorder/{at}.png" if screenshot else None,
        "screenshot_timestamp": at if screenshot else None, "end_screenshot_timestamp": at + 1.0,
        "num_events": 1, "events": [click], "cursor_position": [3, 4],
        "monitor": MONITOR, "burst_id": 7, "scale_factor": 1.0})


def put(client, session, index, at, screenshot=True):
    files = {"screenshot": (f"{at}.png", png(), "image/png")} if screenshot else None
    response = client.put(f"/events/{session}-{index:06d}",
                          data={"aggregation": row(at, screenshot)}, files=files)
    assert response.status_code == 201, response.text


def client_for(tmp_path, actions=(), dense_caption=""):
    def annotate_request(request):
        return Annotation(actions=list(actions), dense_caption=dense_caption,
                          sanitization=request.sanitization)
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2))
    return TestClient(create_app(config, annotate_request=annotate_request))


def wait_for_annotation(data_dir, session, chunk):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        record = FileActivityCaptions(devices_dir(data_dir)).get(session, chunk)
        if record is not None and record.status == "done":
            return
        time.sleep(0.02)
    raise AssertionError(f"chunk {chunk} of {session} was not annotated")


def test_events_of_every_device_come_back_as_one_list_ordered_by_time(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, DESKTOP, 0, 1001.0)
        put(client, LAPTOP, 1, 1002.0)

        newest_first = client.get("/events").json()
        assert [e["id"] for e in newest_first["data"]] == [
            f"{LAPTOP}-000001", f"{DESKTOP}-000000", f"{LAPTOP}-000000"]

        first = client.get("/events", params={"order": "asc", "limit": 2}).json()
        assert first["has_more"]
        rest = client.get("/events", params={"order": "asc", "limit": 2,
                                             "after": first["last_id"]}).json()
        assert [e["id"] for e in first["data"] + rest["data"]] == [
            f"{LAPTOP}-000000", f"{DESKTOP}-000000", f"{LAPTOP}-000001"]
        assert not rest["has_more"]

        only = client.get("/events", params={"device": "desktop"}).json()["data"]
        assert [e["device_id"] for e in only] == ["desktop"]


def test_an_event_shows_its_inputs_screenshot_and_nothing_of_the_recorders_bookkeeping(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        event = client.get(f"/events/{LAPTOP}-000000").json()
        assert event == {
            "id": f"{LAPTOP}-000000", "device_id": "laptop", "timestamp": 1000.0,
            "screenshot_url": f"/events/{LAPTOP}-000000/screenshot",
            "inputs": [{"type": "mouse_down", "timestamp": 1000.5, "button": "left",
                        "x": 3, "y": 4}],
            "caption": None, "sequence": 0, "sequence_caption": None, "indices": {}, "moments": []}
        assert client.get(event["screenshot_url"]).content == png()


def test_a_caption_lands_on_the_rows_its_frames_were_made_from(tmp_path):
    # The first row has no screenshot, so frame 1 of the chunk is the second row.
    with client_for(tmp_path, [Action(start=1, end=1, caption="Clicked.")], "A terminal.") as client:
        put(client, LAPTOP, 0, 1000.0, screenshot=False)
        put(client, LAPTOP, 1, 1001.0)
        put(client, LAPTOP, 2, 1002.0)
        wait_for_annotation(tmp_path, LAPTOP, 0)

        events = client.get("/events", params={"order": "asc"}).json()["data"]
        assert [(e["screenshot_url"] is not None, e["caption"]) for e in events] == [
            (False, None), (True, "Clicked."), (True, None)]
        # Both rows of the first sequence carry its description; the row after it is in the next, not yet captioned.
        assert [(e["sequence"], e["sequence_caption"]) for e in events] == [
            (0, "A terminal."), (0, "A terminal."), (1, None)]


def test_a_users_caption_replaces_the_models_until_it_is_cleared(tmp_path):
    with client_for(tmp_path, [Action(start=1, end=2, caption="Clicked.")]) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1001.0)
        put(client, LAPTOP, 2, 1002.0)
        wait_for_annotation(tmp_path, LAPTOP, 0)
        event_id = f"{LAPTOP}-000000"

        patched = client.patch(f"/events/{event_id}", json={"caption": "Opened the menu."})
        assert patched.json()["caption"] == "Opened the menu."
        assert client.get(f"/events/{event_id}").json()["caption"] == "Opened the menu."

        cleared = client.patch(f"/events/{event_id}", json={"caption": None})
        assert cleared.json()["caption"] == "Clicked."


def test_a_deleted_event_is_gone_from_the_api_but_its_row_and_screenshot_stay_on_disk(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1001.0)
        event_id = f"{LAPTOP}-000000"

        assert client.delete(f"/events/{event_id}").status_code == 204
        assert client.get(f"/events/{event_id}").status_code == 404
        assert client.get(f"/events/{event_id}/screenshot").status_code == 404
        assert client.patch(f"/events/{event_id}", json={"caption": "x"}).status_code == 404
        assert [e["id"] for e in client.get("/events").json()["data"]] == [f"{LAPTOP}-000001"]
        # A client paging from the deleted event still gets what follows it.
        after = client.get("/events", params={"order": "asc", "after": event_id}).json()
        assert [e["id"] for e in after["data"]] == [f"{LAPTOP}-000001"]

    stored = FileEvents(devices_dir(tmp_path)).rows(LAPTOP)
    assert len(stored) == 2
    assert stored[0].burst_id == 7
    assert open(stored[0].screenshot_path, "rb").read() == png()


def test_a_store_moved_to_another_data_directory_keeps_its_screenshots_and_captions(tmp_path):
    with client_for(tmp_path / "before", [Action(start=1, end=2, caption="Clicked.")]) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1001.0)
        put(client, LAPTOP, 2, 1002.0)
        wait_for_annotation(tmp_path / "before", LAPTOP, 0)
    shutil.move(tmp_path / "before", tmp_path / "after")

    with client_for(tmp_path / "after") as client:
        events = client.get("/events", params={"order": "asc"}).json()["data"]
        # The third row is in the next chunk, which has no caption yet.
        assert [e["caption"] for e in events] == ["Clicked.", "Clicked.", None]
        assert client.get(f"/events/{LAPTOP}-000000/screenshot").content == png()
    frames = FileActivityCaptions(devices_dir(tmp_path / "after")).get(LAPTOP, 0).frames
    assert [open(frame.screenshot_path, "rb").read() for frame in frames] == [png(), png()]


def test_paging_after_an_unknown_event_is_refused(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/events", params={"after": f"{LAPTOP}-000009"}).status_code == 400
        assert client.get("/events", params={"after": "nonsense"}).status_code == 400


def test_activity_counts_the_events_between_each_pair_of_edges(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 100.0)
        put(client, LAPTOP, 1, 150.0)
        put(client, DESKTOP, 0, 200.0)
        put(client, LAPTOP, 2, 350.0)
        client.delete(f"/events/{LAPTOP}-000002")

        body = client.get("/events/activity", params={"edges": "0,100,200,300,400"}).json()
        assert body == {"edges": [0.0, 100.0, 200.0, 300.0, 400.0], "counts": [0, 2, 1, 0]}
        laptop = client.get("/events/activity", params={"edges": "0,400", "device": "laptop"}).json()
        assert laptop["counts"] == [2]

        assert client.get("/events/activity", params={"edges": "100"}).status_code == 400
        assert client.get("/events/activity", params={"edges": "200,100"}).status_code == 400
        assert client.get("/events/activity", params={"edges": "a,b"}).status_code == 400


def test_a_row_uploaded_after_a_read_is_in_every_later_read(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        assert [e["id"] for e in client.get("/events").json()["data"]] == [f"{LAPTOP}-000000"]
        assert client.get("/events/activity", params={"edges": "0,5000"}).json()["counts"] == [1]

        put(client, LAPTOP, 1, 3000.0)
        assert [e["id"] for e in client.get("/events").json()["data"]] == [
            f"{LAPTOP}-000001", f"{LAPTOP}-000000"]
        assert client.get("/events/activity", params={"edges": "0,5000"}).json()["counts"] == [2]
        later = client.get("/sessions", params={"since": 2000, "until": 4000}).json()["data"]
        assert [(s["id"], s["event_count"]) for s in later] == [(LAPTOP, 2)]


def test_a_time_range_lists_only_the_events_and_sessions_inside_it(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1100.0)
        put(client, DESKTOP, 0, 5000.0)

        inside = client.get("/events", params={"since": 1050, "until": 6000}).json()["data"]
        assert [e["id"] for e in inside] == [f"{DESKTOP}-000000", f"{LAPTOP}-000001"]
        sessions = client.get("/sessions", params={"since": 4000, "until": 6000}).json()["data"]
        assert [s["id"] for s in sessions] == [DESKTOP]
        indices = client.get("/indices", params={"since": 4000, "until": 6000}).json()
        assert sum(b["rows"] for b in indices["buckets"]) == 1


def test_an_input_the_recorder_wrote_in_the_row_before_is_listed_under_its_own_event(tmp_path):
    def record(at, shot, end, clicks):
        events = [{"timestamp": t, "monitor_index": 0, "monitor": MONITOR, "event_type": "mouse_down",
                   "details": {"x": 3, "y": 4, "button": "left"}, "cursor_position": [3, 4]}
                  for t in clicks]
        return json.dumps({
            "timestamp": at, "reason": "click_end_stale", "event_type": "click",
            "request_state": "end", "screenshot_path": f"/recorder/{shot}.png",
            "screenshot_timestamp": shot, "end_screenshot_timestamp": end,
            "num_events": len(events), "events": events, "cursor_position": None,
            "monitor": MONITOR, "burst_id": 7, "scale_factor": 1.0})

    with client_for(tmp_path) as client:
        # The recorder wrote the first row while it was its last: it holds the
        # click that the second row's screenshot was taken before.
        for index, body in enumerate([record(1000.0, 1000.5, None, [1001.7]),
                                      record(1001.7, 1001.5, None, [])]):
            response = client.put(f"/events/{LAPTOP}-{index:06d}", data={"aggregation": body},
                                  files={"screenshot": (f"{index}.png", png(), "image/png")})
            assert response.status_code == 201, response.text

        events = client.get(f"/sessions/{LAPTOP}").json()["events"]
        assert [[i["timestamp"] for i in e["inputs"]] for e in events] == [[], [1001.7]]
