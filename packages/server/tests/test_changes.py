"""``GET /changes``: a client holding a cursor is told which sessions changed, and only the events that did."""

from annotate.label import Action
from test_events import DESKTOP, LAPTOP, client_for, put, wait_for_annotation


def changes(client, cursor=None, **params):
    return client.get("/changes", params={k: v for k, v in {"cursor": cursor, **params}.items() if v is not None}).json()


def test_without_a_cursor_every_session_in_the_range_is_listed_whole(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, DESKTOP, 0, 2000.0)

        body = changes(client, since=1500.0, events=DESKTOP)
        assert [s["id"] for s in body["sessions"]] == [DESKTOP]
        [desktop] = body["sessions"]
        assert desktop["whole"] and [e["id"] for e in desktop["events"]] == [f"{DESKTOP}-000000"]
        assert desktop["event_count"] == 1 and desktop["removed"] == []
        assert body["cursor"]


def test_nothing_is_listed_until_something_changes(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        cursor = changes(client)["cursor"]

        again = changes(client, cursor)
        assert again["sessions"] == [] and again["cursor"] == cursor


def test_a_row_uploaded_after_the_cursor_comes_back_alone(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0, annotate=False)
        put(client, DESKTOP, 0, 1100.0, annotate=False)
        cursor = changes(client, events=f"{LAPTOP},{DESKTOP}")["cursor"]

        put(client, LAPTOP, 1, 1200.0, annotate=False)
        body = changes(client, cursor, events=f"{LAPTOP},{DESKTOP}")
        [laptop] = body["sessions"]
        assert laptop["id"] == LAPTOP and laptop["end"] == 1200.0 and laptop["event_count"] == 2
        assert not laptop["whole"]
        assert [e["id"] for e in laptop["events"]] == [f"{LAPTOP}-000001"]
        assert body["cursor"] != cursor

        # A session the client does not hold the events of is listed without them.
        put(client, DESKTOP, 1, 1300.0, annotate=False)
        [desktop] = changes(client, body["cursor"], events=LAPTOP)["sessions"]
        assert desktop["id"] == DESKTOP and desktop["events"] is None


def test_a_caption_changes_the_rows_it_lands_on(tmp_path):
    with client_for(tmp_path, [Action(start=1, end=2, caption="Typed.")], "An editor.") as client:
        put(client, LAPTOP, 0, 1000.0, screenshot=False)
        cursor = changes(client, events=LAPTOP)["cursor"]
        put(client, LAPTOP, 1, 1001.0)
        put(client, LAPTOP, 2, 1002.0)
        wait_for_annotation(tmp_path, LAPTOP, 0)

        # The first chunk is rows 0 and 1, its only frame row 1; row 2 starts the next, not yet captioned.
        [laptop] = changes(client, cursor, events=LAPTOP)["sessions"]
        assert laptop["captioned"] == 1
        assert [(e["id"][-6:], e["caption"], e["sequence_caption"]) for e in laptop["events"]] == [
            ("000000", None, "An editor."), ("000001", "Typed.", "An editor."), ("000002", None, None)]


def test_an_edit_and_a_deletion_come_back_as_a_changed_event_and_a_removed_id(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0, annotate=False)
        put(client, LAPTOP, 1, 1001.0, annotate=False)
        put(client, LAPTOP, 2, 1002.0, annotate=False)
        cursor = changes(client, events=LAPTOP)["cursor"]

        client.patch(f"/events/{LAPTOP}-000001", json={"caption": "Mine."})
        client.delete(f"/events/{LAPTOP}-000002")
        [laptop] = changes(client, cursor, events=LAPTOP)["sessions"]
        assert [(e["id"][-6:], e["caption"]) for e in laptop["events"]] == [("000001", "Mine.")]
        assert laptop["removed"] == [f"{LAPTOP}-000002"]
        assert laptop["event_count"] == 2


def test_a_cursor_from_another_run_of_the_server_gets_everything(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        body = changes(client, "deadbeef:7", events=LAPTOP)
        [laptop] = body["sessions"]
        assert laptop["whole"] and len(laptop["events"]) == 1
        assert not body["cursor"].startswith("deadbeef:")


def test_a_session_changed_outside_the_range_is_not_listed(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, DESKTOP, 0, 5000.0)
        cursor = changes(client)["cursor"]

        put(client, LAPTOP, 1, 1001.0)
        assert changes(client, cursor, since=4000.0)["sessions"] == []
        assert [s["id"] for s in changes(client, cursor)["sessions"]] == [LAPTOP]
