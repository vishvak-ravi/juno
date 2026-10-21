"""The model input we build must be the one Tada's own labeler sends.

The reference is Tada's ``Labeler.label_chunk`` itself, run up to its
``litellm.completion`` call, which is captured instead of sent. ``tests/tada/``
holds the Tada files it needs, unmodified, from github.com/GeneralUserModels/tada
at 25a28f7 (Apache-2.0, see ``tests/tada/LICENSE``).

Three sources of aggregations are checked: a recording through the Wayland
sources with a simulated portal helper and simulated kernel input, a recording
through fake sources behind the ``Backend`` interface, and hand-written rows.
"""

import base64
import importlib.util
import io
import json
import re
import sys
import time
import types
from pathlib import Path

import litellm
import numpy as np
import pytest
from napsack.record.models.aggregation import AggregationRequest, ProcessedAggregation
from PIL import Image

from annotate.label import annotate
from juno.sessions import read_aggregations
from annotate.request import DEFAULT_CHUNK_SIZE, build_requests, draw_cursor, to_messages
from annotate.sanitize import sanitize
from capture.recorder import Recorder
from capture.sources.base import Backend
from capture.sources.wayland.evdev_input import EvdevInputSource
from capture.sources.fake import FakeCursor, FakeScreenSource
from capture.sources.wayland.portal_screen import PortalScreenSource
from juno.config import AnnotateConfig

from capture.sources.fake import ManualInput, type_and_click

TADA_DIR = Path(__file__).with_name("tada")
# The fake portal helper lives with the capture tests; both packages speak its protocol.
FAKE_HELPER = str(Path(__file__).parents[2] / "capture" / "tests" / "fake_helper.py")
MONITOR = {"left": 100, "top": 0, "width": 200, "height": 100}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TadaLabeler:
    """Tada's labeler with the model call captured."""

    def __init__(self, labeler, calls):
        self.labeler = labeler
        self.calls = calls

    def sent(self, processed):
        """The ``litellm.completion`` arguments for one chunk; None if Tada sends nothing."""
        self.calls.clear()
        self.labeler.label_chunk(processed)
        return self.calls[0] if self.calls else None


@pytest.fixture
def tada(monkeypatch):
    # labeler.py imports shared.model_catalog and stubs napsack's Tinfoil client.
    catalog = load_module("shared.model_catalog", TADA_DIR / "model_catalog.py")
    monkeypatch.setitem(sys.modules, "shared", types.ModuleType("shared"))
    monkeypatch.setitem(sys.modules, "shared.model_catalog", catalog)
    monkeypatch.setitem(sys.modules, "napsack.label.clients.tinfoil",
                        types.SimpleNamespace(TinfoilClient=object))
    module = load_module("tada_labeler", TADA_DIR / "labeler.py")

    calls = []

    def completion(**params):
        calls.append(params)
        message = types.SimpleNamespace(content='{"actions": [], "dense_caption": ""}')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    def no_retry(seconds):
        raise AssertionError("Tada's labeler went into its retry sleep")

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(module, "time", types.SimpleNamespace(sleep=no_retry))
    return TadaLabeler(module.Labeler(api_key="not-sent-anywhere"), calls)


def decode(part):
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")


def images(messages):
    return [decode(part) for part in messages[0]["content"] if part["type"] == "image_url"]


def assert_same_model_input(request, sent):
    """``request`` reaches the model as ``sent`` did, bar the pointer we draw in."""
    ours, theirs = to_messages(request), sent["messages"]
    assert [m["role"] for m in ours] == [m["role"] for m in theirs] == ["user"]
    our_parts, their_parts = ours[0]["content"], theirs[0]["content"]
    assert [p["type"] for p in our_parts] == [p["type"] for p in their_parts]
    assert ([p["text"] for p in our_parts if p["type"] == "text"]
            == [p["text"] for p in their_parts if p["type"] == "text"])
    assert str(request) == "\n".join(p["text"] for p in their_parts if p["type"] == "text")
    # Tada's frames come from mss with the pointer in them; ours get it drawn.
    for frame, mine, tadas in zip(request.frames, images(ours), images(theirs), strict=True):
        expected = draw_cursor(tadas, frame.cursor, frame.monitor)
        assert mine.size == expected.size and mine.tobytes() == expected.tobytes()


def in_time_order(processed):
    """``processed`` sorted by screenshot time, those without a screenshot last."""
    return sorted(processed, key=lambda p: (p.request.screenshot_timestamp is None,
                                            p.request.screenshot_timestamp or 0.0))


def assert_matches_tada(tada, aggregations, processed):
    """Chunk for chunk, our requests are what Tada sends for the same aggregations.

    Tada is handed each chunk in time order; what it does with a chunk that is
    not is pinned in ``test_a_chunk_out_of_time_order_is_where_we_differ``.
    """
    assert len(aggregations) == len(processed)
    requests = list(build_requests(aggregations))
    sent = [tada.sent(in_time_order(processed[start:start + DEFAULT_CHUNK_SIZE]))
            for start in range(0, len(processed), DEFAULT_CHUNK_SIZE)]
    sent = [s for s in sent if s is not None]
    assert len(requests) == len(sent)
    for request, chunk_sent in zip(requests, sent):
        assert_same_model_input(request, chunk_sent)
    return requests, sent


def record(backend, tmp_path, play, **kwargs):
    """Record ``play`` and return what was stored and what Tada's labeler would be handed."""
    processed = []
    recorder = Recorder(backend, data_dir=tmp_path, fps=40, poll_interval=0.05,
                        on_aggregation=processed.append, **kwargs)
    recorder.start()
    time.sleep(0.1)
    play()
    time.sleep(0.15)
    assert recorder.stop(), "an event was left out of every aggregation"
    return read_aggregations(recorder.session_dir), processed


def all_text(requests):
    return "\n".join(str(request) for request in requests)


def keys_pressed(requests):
    """Every key the event text names, in the order the model reads them."""
    lines = re.findall(r"Key(?:s|\(s\))? pressed: (.+)", all_text(requests))
    return [key for line in lines for key in line.split("|")]


# -- a) the Wayland sources, fed simulated data ---------------------------


def test_wayland_recording_reaches_the_model_as_tadas_would(tada, tmp_path):
    e = pytest.importorskip("evdev").ecodes

    class SimulatedEvdev(EvdevInputSource):
        """No /dev/input: the test plays kernel events through ``dispatch``."""

        def start(self, listener):
            self._ecodes = e
            self._listener = listener

    screen = PortalScreenSource(command=[sys.executable, FAKE_HELPER, "desktop"],
                                token_file=tmp_path / "token", start_timeout=10.0)
    keyboard_and_mouse = SimulatedEvdev()
    # Wired as capture.sources.wayland.create does.
    backend = Backend(name="wayland", screen=screen, input=keyboard_and_mouse, cursor=screen)

    def play():
        deadline = time.monotonic() + 5.0
        while screen.position() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        for ev_type, code, value in [
                (e.EV_KEY, e.KEY_LEFTSHIFT, 1), (e.EV_KEY, e.KEY_H, 1), (e.EV_KEY, e.KEY_H, 0),
                (e.EV_KEY, e.KEY_LEFTSHIFT, 0), (e.EV_KEY, e.KEY_I, 1), (e.EV_KEY, e.KEY_I, 0),
                (e.EV_KEY, e.BTN_LEFT, 1), (e.EV_KEY, e.BTN_LEFT, 0),
                (e.EV_REL, e.REL_WHEEL, -1)]:
            keyboard_and_mouse.dispatch(ev_type, code, value, time.time())
            time.sleep(0.02)

    aggregations, processed = record(backend, tmp_path, play)
    requests, sent = assert_matches_tada(tada, aggregations, processed)

    text = all_text(requests)
    assert keys_pressed(requests) == ["shift", "H", "i"]
    # The pointer at (80, 50) on the 320x200 monitor, in napsack's 0-1000 units.
    assert "Mouse clicked left at (250, 250)" in text
    assert "Scrolled down" in text
    frames = [frame for request in requests for frame in request.frames]
    assert {frame.cursor for frame in frames} == {(80, 50)}
    # The pointer is drawn: the only place our images differ from Tada's.
    assert images(to_messages(requests[0]))[0].tobytes() != images(sent[0]["messages"])[0].tobytes()


# -- b) fake sources behind the Backend interface -------------------------


@pytest.mark.parametrize("cursor_position", [(10, 20), None])
def test_fake_backend_recording_reaches_the_model_as_tadas_would(tada, tmp_path, cursor_position):
    backend = Backend("fake", FakeScreenSource(change_every=1), ManualInput(),
                      FakeCursor(cursor_position))
    aggregations, processed = record(
        backend, tmp_path, lambda: type_and_click(backend.input.listener))
    requests, sent = assert_matches_tada(tada, aggregations, processed)

    assert keys_pressed(requests) == ["h", "i"]
    assert "Mouse clicked left at" in all_text(requests)
    if cursor_position is None:
        # Nothing to draw, so the images are Tada's pixel for pixel.
        assert ([image.tobytes() for image in images(to_messages(requests[0]))]
                == [image.tobytes() for image in images(sent[0]["messages"])])


# -- c) hand-written aggregation rows -------------------------------------


def event(timestamp, event_type, details, cursor=(150, 50)):
    return {"timestamp": timestamp, "monitor_index": 0, "monitor": MONITOR,
            "event_type": event_type, "details": details, "cursor_position": cursor}


def row(tmp_path, shot, events, screenshot=True):
    path = None
    if screenshot:
        path = tmp_path / f"{shot}.png"
        Image.new("RGB", (400, 200), (int(shot) % 256, 128, 128)).save(path)
    return {"timestamp": shot, "reason": "key_start", "event_type": "key",
            "request_state": "start", "screenshot_path": str(path) if path else None,
            "screenshot_timestamp": shot if path else None,
            "end_screenshot_timestamp": shot + 1.0 if path else None,
            "num_events": len(events), "events": events,
            "cursor_position": events[0]["cursor_position"] if events else None,
            "monitor": MONITOR, "burst_id": 1, "scale_factor": 1.0}


def write_session(tmp_path, rows):
    (tmp_path / "raw_aggregations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    return read_aggregations(tmp_path)


def as_processed(stored):
    """A stored row as the in-memory aggregation Tada's labeler is handed."""
    screenshot = None
    if stored["screenshot_path"] is not None:
        with Image.open(stored["screenshot_path"]) as image:
            screenshot = types.SimpleNamespace(data=np.asarray(image.convert("RGB")))
    request = AggregationRequest(
        timestamp=stored["timestamp"], end_timestamp=None, reason=stored["reason"],
        event_type=stored["event_type"], request_state=stored["request_state"],
        screenshot=screenshot, screenshot_path=stored["screenshot_path"],
        screenshot_timestamp=stored["screenshot_timestamp"],
        end_screenshot_timestamp=stored["end_screenshot_timestamp"],
        monitor=stored["monitor"], burst_id=stored["burst_id"],
        scale_factor=stored["scale_factor"])
    return ProcessedAggregation(request=request, events=stored["events"])


def test_every_event_kind_is_worded_as_tada_words_it(tada, tmp_path):
    rows = [
        row(tmp_path, 10.0, [event(10.1, "key_press", {"key": "Key.ctrl"}),
                             event(10.2, "key_press", {"key": "c"}),
                             event(10.3, "key_release", {"key": "c"}),
                             event(10.4, "mouse_down", {"button": "Button.right"},
                                   cursor=(300, 100)),
                             event(10.5, "mouse_up", {"button": "Button.right"},
                                   cursor=(300, 100))]),
        row(tmp_path, 11.0, [event(11.1, "mouse_scroll", {"dx": 0, "dy": -1}),
                             event(11.2, "mouse_scroll", {"dx": 0, "dy": -1}),
                             event(11.3, "mouse_move", {}, cursor=(120, 10)),
                             event(11.4, "mouse_move", {}, cursor=(280, 90))]),
        row(tmp_path, 12.0, [event(12.5, "key_press", {"key": "Key.enter"}, cursor=None)]),
        row(tmp_path, 13.0, []),
    ]
    aggregations = write_session(tmp_path, rows)
    (request,), _ = assert_matches_tada(tada, aggregations, [as_processed(r) for r in rows])
    assert [frame.label for frame in request.frames] == [
        "Frame 1", "Frame 2", "Frame 3", "Frame 4"]
    assert "No actions recorded." in request.frames[3].events


def test_rows_are_chunked_as_tadas_labeling_loop_chunks_them(tada, tmp_path):
    rows = [row(tmp_path, 100.0 + i, [event(100.5 + i, "key_press", {"key": "a"})])
            for i in range(DEFAULT_CHUNK_SIZE + 2)]
    aggregations = write_session(tmp_path, rows)
    requests, _ = assert_matches_tada(tada, aggregations, [as_processed(r) for r in rows])
    assert [len(request.frames) for request in requests] == [DEFAULT_CHUNK_SIZE, 2]


def test_an_aggregation_without_a_screenshot_is_dropped_with_its_events(tada, tmp_path):
    rows = [
        row(tmp_path, 10.0, [event(10.5, "key_press", {"key": "a"})]),
        # Falls inside the first frame's window, but Tada never looks at it.
        row(tmp_path, 10.6, [event(10.7, "key_press", {"key": "b"})], screenshot=False),
        row(tmp_path, 11.0, [event(11.5, "key_press", {"key": "c"})]),
    ]
    aggregations = write_session(tmp_path, rows)
    (request,), _ = assert_matches_tada(tada, aggregations, [as_processed(r) for r in rows])
    assert len(request.frames) == 2


def test_a_chunk_out_of_time_order_is_where_we_differ(tada, tmp_path):
    """Tada sorts the event text by screenshot time but leaves the images in arrival order.

    Handed a later aggregation first, it shows the model the later image above
    the earlier events. We keep each image with its own events.
    """
    earlier = row(tmp_path, 10.0, [event(10.5, "key_press", {"key": "a"})])
    later = row(tmp_path, 11.0, [event(11.5, "key_press", {"key": "b"})])
    aggregations = write_session(tmp_path, [later, earlier])
    request = next(build_requests(aggregations))
    sent = tada.sent([as_processed(later), as_processed(earlier)])

    ours, theirs = to_messages(request), sent["messages"]
    assert ([p["text"] for p in ours[0]["content"] if p["type"] == "text"]
            == [p["text"] for p in theirs[0]["content"] if p["type"] == "text"])
    assert "Key pressed: a" in request.frames[0].events

    def stored(aggregation_row):
        with Image.open(aggregation_row["screenshot_path"]) as image:
            return image.convert("RGB").tobytes()

    assert [request.frames[0].cursor, request.frames[1].cursor] == [(150, 50), (150, 50)]
    assert ([Image.open(frame.screenshot_path).convert("RGB").tobytes()
             for frame in request.frames] == [stored(earlier), stored(later)])
    assert [image.tobytes() for image in images(theirs)] == [stored(later), stored(earlier)]


def test_the_request_tada_sends_is_the_one_these_tests_assume(tada, tmp_path):
    """Pins what Tada passes besides the messages, so a change in ``tests/tada/`` shows up here."""
    rows = [row(tmp_path, 10.0, [event(10.5, "key_press", {"key": "a"})])]
    sent = tada.sent([as_processed(r) for r in rows])
    assert sent["model"].startswith("gemini/")
    assert sent["response_mime_type"] == "application/json"
    assert sent["response_schema"]["required"] == ["actions", "dense_caption"]
    prompt = sent["messages"][0]["content"][-1]["text"]
    assert prompt == next(build_requests(write_session(tmp_path, rows))).prompt
    assert "{{" not in prompt and '"dense_caption"' in prompt


def test_our_model_call_is_tadas_model_call(tada, tmp_path):
    """Everything handed to ``litellm.completion``, not only the messages."""
    rows = [row(tmp_path, 10.0, [event(10.5, "key_press", {"key": "a"})]),
            row(tmp_path, 11.0, [event(11.5, "mouse_down", {"button": "Button.left"})])]
    theirs = dict(tada.sent([as_processed(r) for r in rows]))

    # Nothing in these rows is a secret, so sanitizing leaves Tada's input as it is.
    request = sanitize(next(build_requests(write_session(tmp_path, rows))))
    config = AnnotateConfig()
    tada.calls.clear()
    annotation = annotate(request, model=config.model, max_tokens=config.max_tokens,
                          api_key=theirs["api_key"])
    ours = dict(tada.calls[0])

    assert_same_model_input(request, theirs)
    del ours["messages"], theirs["messages"]
    assert ours == theirs
    assert annotation.actions == [] and annotation.dense_caption == ""
