import json
import sys
import types

import pytest
from PIL import Image

from annotate.records import Aggregation, read_aggregations
from annotate.request import build_request, build_requests, draw_cursor, to_messages

MONITOR = {"left": 100, "top": 0, "width": 200, "height": 100}


def event(timestamp, event_type, details, cursor=(150, 50)):
    return {"timestamp": timestamp, "monitor_index": 0, "monitor": MONITOR,
            "event_type": event_type, "details": details, "cursor_position": cursor}


def row(tmp_path, shot, end, events):
    path = tmp_path / f"{shot}.png"
    Image.new("RGB", (400, 200), "white").save(path)
    return {"timestamp": shot, "reason": "key_start", "event_type": "key",
            "request_state": "start", "screenshot_path": str(path),
            "screenshot_timestamp": shot, "end_screenshot_timestamp": end,
            "num_events": len(events), "events": events,
            "cursor_position": events[0]["cursor_position"] if events else None,
            "monitor": MONITOR, "burst_id": 1, "scale_factor": 1.0}


@pytest.fixture
def rows(tmp_path):
    return [
        row(tmp_path, 10.0, 12.0, [event(10.5, "key_press", {"key": "h"}),
                                   event(10.6, "key_release", {"key": "h"}),
                                   event(11.0, "mouse_down", {"button": "Button.left"},
                                         cursor=(300, 100))]),
        row(tmp_path, 12.0, 13.0, []),
    ]


@pytest.fixture
def session(tmp_path, rows):
    (tmp_path / "raw_aggregations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    return tmp_path


def test_a_session_round_trips_through_the_stored_type(session, rows):
    aggregations = read_aggregations(session)
    assert [json.loads(a.model_dump_json()) for a in aggregations] == json.loads(json.dumps(rows))


def test_request_text_is_what_tadas_labeler_sends(session, rows, monkeypatch):
    # Tada's labeler stubs this optional client before importing napsack's.
    monkeypatch.setitem(sys.modules, "napsack.label.clients.tinfoil",
                        types.SimpleNamespace(TinfoilClient=object))
    from napsack.label.clients.litellm import LiteLLMClient  # noqa: PLC0415 - needs the stub
    from napsack.label.models import Aggregation as NapsackAggregation  # noqa: PLC0415
    from napsack.record.sanitize import sanitize_records  # noqa: PLC0415

    request = build_request(read_aggregations(session))
    messages = to_messages(request)

    client = LiteLLMClient(model_name="hosted_vllm/any")
    per_frame = [NapsackAggregation.from_dict(r).to_prompt(f"Frame {i + 1}")
                 for i, r in enumerate(sanitize_records(rows))]
    expected = client._build_messages(
        request.prompt,
        client.upload_images([r["screenshot_path"] for r in rows], per_frame_text=per_frame))

    def text_parts(chat):
        return [part["text"] for part in chat[0]["content"] if part["type"] == "text"]

    assert text_parts(messages) == text_parts(expected)
    assert [part["type"] for part in messages[0]["content"]] == [
        part["type"] for part in expected[0]["content"]]
    assert "Keys pressed" not in request.prompt and "{{" not in request.prompt
    assert "Mouse clicked left at (1000, 1000)" in str(request)
    assert str(request).endswith(request.prompt)


def test_frames_carry_the_pointer_forward(session):
    first, second = build_request(read_aggregations(session)).frames
    assert first.cursor == (150, 50)   # the first event after the screenshot
    assert second.cursor == (300, 100)  # no events: where the last one left it


def test_requests_are_chunked(session):
    aggregations = read_aggregations(session)
    assert [len(r.frames) for r in build_requests(aggregations, chunk_size=1)] == [1, 1]
    assert [len(r.frames) for r in build_requests(aggregations)] == [2]


def test_draw_cursor_marks_the_pointer_and_leaves_the_original_alone():
    image = Image.new("RGB", (2000, 1000), "white")
    drawn = draw_cursor(image, (150, 50), MONITOR)
    # Desktop (150, 50) on a 200x100 monitor at left=100 is pixel (500, 500);
    # the arrow is 20 px tall and hangs down and right from there.
    assert drawn.getpixel((504, 509)) == (0, 0, 0)
    assert drawn.getpixel((495, 500)) == (255, 255, 255)
    assert image.getpixel((504, 509)) == (255, 255, 255)
    assert draw_cursor(image, None, MONITOR) is image
