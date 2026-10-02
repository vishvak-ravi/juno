import json
import re
import time

import pytest

from capture.check import check_session, latest_session
from capture.check import main as check_main
from capture.recorder import Recorder
from capture.sources.base import Backend, InputSource
from capture.sources.fake import FakeCursor, FakeScreenSource

from conftest import LEFT, RIGHT


class ManualInput(InputSource):
    """Hands the listener to the test so it can play events itself."""

    def __init__(self):
        self.listener = None
        self.stopped = False

    def start(self, listener):
        self.listener = listener

    def stop(self):
        self.stopped = True


@pytest.fixture
def backend():
    return Backend(
        name="fake",
        screen=FakeScreenSource(monitors=[LEFT, RIGHT], change_every=1),
        input=ManualInput(),
        cursor=FakeCursor((10, 20)),
    )


def make(backend, tmp_path, **kwargs):
    kwargs.setdefault("fps", 40)
    kwargs.setdefault("poll_interval", 0.05)
    return Recorder(backend, data_dir=tmp_path, **kwargs)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def type_and_click(listener):
    for key in ("h", "i"):
        listener.on_key(key, True)
        time.sleep(0.02)
        listener.on_key(key, False)
        time.sleep(0.02)
    listener.on_click("Button.left", True)
    time.sleep(0.02)
    listener.on_click("Button.left", False)


def record(backend, tmp_path, play=type_and_click, **kwargs):
    recorder = make(backend, tmp_path, **kwargs)
    recorder.start()
    time.sleep(0.1)
    play(backend.input.listener)
    time.sleep(0.15)
    complete = recorder.stop()
    return recorder, complete


def test_activity_is_logged_as_napsack_session(backend, tmp_path):
    recorder, complete = record(backend, tmp_path)
    session = recorder.session_dir

    assert complete, "an event was left out of every aggregation"
    assert session.parent == tmp_path / "screen" / "sessions"
    assert re.fullmatch(r"session_\d{8}_\d{6}", session.name)

    events = read_jsonl(session / "input_events.jsonl")
    assert [e["event_type"] for e in events] == [
        "key_press", "key_release", "key_press", "key_release", "mouse_down", "mouse_up"]
    assert events[0]["details"] == {"key": "h"}
    assert events[4]["details"] == {"x": 10, "y": 20, "button": "Button.left"}
    assert events[4]["cursor_position"] == [10, 20]
    # napsack's own event log is written too.
    assert len((session / "events.jsonl").read_text().splitlines()) == 6

    aggregations = read_jsonl(session / "raw_aggregations.jsonl")
    reasons = {a["reason"] for a in aggregations}
    assert {"key_start_stale", "key_end_stale",
            "click_start_stale", "click_end_stale"} <= reasons
    for record_ in aggregations:
        assert set(record_) == {
            "timestamp", "reason", "event_type", "request_state", "screenshot_path",
            "screenshot_timestamp", "end_screenshot_timestamp", "num_events", "events",
            "cursor_position", "monitor", "burst_id", "scale_factor"}
        assert record_["monitor"] == LEFT.to_dict()
        assert (session / "screenshots").joinpath(
            record_["screenshot_path"].rsplit("/", 1)[-1]).is_file()
    # Every event landed in exactly one aggregation.
    assert sum(a["num_events"] for a in aggregations) == 6
    assert recorder.processed_aggregations == len(aggregations)

    pngs = list((session / "screenshots").glob("*.png"))
    assert len(pngs) == len(aggregations)
    assert len(read_jsonl(session / "screenshots.jsonl")) == len(aggregations)
    assert backend.input.stopped and backend.screen.monitors() == []


def test_mouse_moves_are_dropped_by_default_like_tada(backend, tmp_path):
    def play(listener):
        listener.on_move((30, 30))
        listener.on_key("a", True)

    recorder, _ = record(backend, tmp_path, play=play)
    events = read_jsonl(recorder.session_dir / "input_events.jsonl")
    assert [e["event_type"] for e in events] == ["key_press"]


def test_moves_are_recorded_when_enabled(backend, tmp_path):
    recorder, complete = record(backend, tmp_path, disable=[],
                                play=lambda listener: listener.on_move((30, 30)))
    assert complete
    aggregations = read_jsonl(recorder.session_dir / "raw_aggregations.jsonl")
    assert {a["event_type"] for a in aggregations} == {"move"}


def test_monitor_switch_mid_burst_captures_the_new_monitor(backend, tmp_path):
    def play(listener):
        listener.on_key("a", True)
        backend.cursor.set((700, 10))
        time.sleep(0.1)
        listener.on_key("b", True)

    recorder, complete = record(backend, tmp_path, play=play)
    assert complete
    aggregations = read_jsonl(recorder.session_dir / "raw_aggregations.jsonl")
    switched = [a for a in aggregations if a["reason"] == "key_mid_monitor_switch"]
    assert len(switched) == 1
    assert switched[0]["monitor"] == RIGHT.to_dict()


def test_no_screenshots_flag_writes_events_only(backend, tmp_path):
    recorder, _ = record(backend, tmp_path, save_screenshots=False)
    session = recorder.session_dir
    assert list((session / "screenshots").glob("*.png")) == []
    aggregations = read_jsonl(session / "raw_aggregations.jsonl")
    assert aggregations and all(a["screenshot_path"] == "" for a in aggregations)


def test_scaled_screenshots_record_their_scale_factor(backend, tmp_path):
    from PIL import Image
    recorder, _ = record(backend, tmp_path, scale=0.5, target_dpi=None)
    session = recorder.session_dir
    aggregations = read_jsonl(session / "raw_aggregations.jsonl")
    assert {a["scale_factor"] for a in aggregations} == {0.5}
    with Image.open(next((session / "screenshots").glob("*.png"))) as img:
        assert img.size == (320, 180)


def test_on_aggregation_hook_gets_the_frame_and_cannot_break_recording(backend, tmp_path):
    seen = []

    def hook(processed):
        seen.append(processed.screenshot.data.shape)
        raise RuntimeError("labeler down")

    recorder, complete = record(backend, tmp_path, on_aggregation=hook)
    assert complete
    assert len(seen) == recorder.processed_aggregations > 0
    assert set(seen) == {(360, 640, 3)}


def test_session_dir_override(backend, tmp_path):
    target = tmp_path / "exact"
    recorder = Recorder(backend, session_dir=target)
    assert recorder.session_dir == target and target.is_dir()


def test_requires_a_destination(backend):
    with pytest.raises(ValueError):
        Recorder(backend)


def test_stop_is_idempotent_and_safe_before_start(backend, tmp_path):
    recorder = make(backend, tmp_path)
    assert recorder.stop() is True
    recorder.start()
    assert recorder.stop() is True
    assert recorder.stop() is True


def test_failed_start_cleans_up_and_raises(backend, tmp_path):
    def boom():
        raise PermissionError("portal denied")

    backend.screen.start = boom
    recorder = make(backend, tmp_path)
    with pytest.raises(PermissionError):
        recorder.start()
    assert recorder.running is False
    assert backend.input.listener is None
    assert not recorder.event_queue._running


# ---- the acceptance checker, on real recorder output ----

def test_check_passes_on_a_recorded_session(backend, tmp_path, capsys):
    recorder, _ = record(backend, tmp_path)
    report = check_session(recorder.session_dir)
    assert report.ok, report.format()
    assert report.total_events == 6
    assert report.events["key_press"] == 2
    assert report.with_screenshot == report.aggregations > 0
    assert report.black_screenshots == 0 and report.orphaned_events == 0

    assert latest_session(tmp_path) == recorder.session_dir
    assert check_main(["--data-dir", str(tmp_path)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_check_survives_a_moved_session(backend, tmp_path):
    recorder, _ = record(backend, tmp_path)
    moved = tmp_path / "archive"
    recorder.session_dir.rename(moved)
    assert check_session(moved).ok


def test_check_fails_on_black_screenshots(backend, tmp_path):
    from PIL import Image
    recorder, _ = record(backend, tmp_path)
    for png in (recorder.session_dir / "screenshots").glob("*.png"):
        Image.new("RGB", (64, 36)).save(png)
    report = check_session(recorder.session_dir)
    assert not report.ok
    assert any("black" in p for p in report.problems)


def test_check_fails_when_screenshots_are_missing(backend, tmp_path):
    recorder, _ = record(backend, tmp_path, save_screenshots=False)
    report = check_session(recorder.session_dir)
    assert any("no aggregation has a screenshot" in p for p in report.problems)


def test_check_fails_on_orphaned_events(backend, tmp_path):
    recorder, _ = record(backend, tmp_path)
    path = recorder.session_dir / "raw_aggregations.jsonl"
    records = read_jsonl(path)
    for record_ in records:
        record_["events"] = []
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    report = check_session(recorder.session_dir)
    assert report.orphaned_events == 6
    assert any("in no aggregation" in p for p in report.problems)


def test_check_fails_on_an_empty_session(tmp_path, capsys):
    report = check_session(tmp_path)
    assert report.problems == ["no input events were logged", "no aggregations were written"]
    assert check_main([str(tmp_path)]) == 1
    assert check_main(["--data-dir", str(tmp_path / "nothing")]) == 2
