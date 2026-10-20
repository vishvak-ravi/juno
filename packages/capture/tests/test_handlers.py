import numpy as np
import pytest
from napsack.record.models import EventType

from capture.handlers import InputHandler, MonitorLocator, ScreenshotLoop
from capture.sources.base import Frame, Monitor, ScreenSource
from capture.sources.fake import FakeCursor, FakeScreenSource

from conftest import LEFT, RIGHT


# ---- InputHandler: event shape must match napsack's InputEventHandler ----

def test_key_event_matches_napsack_schema(queue, screen, cursor):
    handler = InputHandler(queue, screen, cursor)
    handler.on_key("a", True, timestamp=100.0)
    handler.on_key("Key.enter", False, timestamp=100.5)

    press, release = (e.to_dict() for e in queue.items)
    assert press == {
        "timestamp": 100.0,
        "monitor_index": 0,
        "monitor": {"left": 0, "top": 0, "width": 640, "height": 360},
        "event_type": "key_press",
        "details": {"key": "a"},
        "cursor_position": (10, 20),
    }
    assert release["event_type"] == "key_release"
    assert release["details"] == {"key": "Key.enter"}


def test_click_and_scroll_details(queue, screen, cursor):
    handler = InputHandler(queue, screen, cursor)
    handler.on_click("Button.left", True, pos=(700, 50))
    handler.on_click("Button.left", False, pos=(700, 50))
    handler.on_scroll(0, -1)

    down, up, scroll = queue.items
    assert down.event_type is EventType.MOUSE_DOWN
    assert up.event_type is EventType.MOUSE_UP
    assert down.details == {"x": 700, "y": 50, "button": "Button.left"}
    assert down.cursor_position == (700, 50)
    # An explicit position on the second monitor wins over the cursor source.
    assert down.monitor_index == 1
    assert down.monitor == RIGHT.to_dict()
    # Scroll had no position, so it came from the cursor source.
    assert scroll.details == {"x": 10, "y": 20, "dx": 0, "dy": -1}
    assert scroll.monitor_index == 0


def test_timestamp_defaults_to_wall_clock(queue, screen, cursor, monkeypatch):
    monkeypatch.setattr("capture.handlers.time.time", lambda: 1234.5)
    InputHandler(queue, screen, cursor).on_key("x", True)
    assert queue.items[0].timestamp == 1234.5


def test_unknown_cursor_logs_null_position_on_first_monitor(queue, screen):
    handler = InputHandler(queue, screen, FakeCursor(None))
    handler.on_click("Button.right", True)
    handler.on_key("b", True)

    click, key = queue.items
    assert click.cursor_position is None
    assert click.details == {"x": None, "y": None, "button": "Button.right"}
    assert click.monitor_index == 0 and click.monitor == LEFT.to_dict()
    assert key.cursor_position is None


def test_position_outside_every_monitor_falls_back_to_first(queue, screen, cursor):
    InputHandler(queue, screen, cursor).on_click("Button.left", True, pos=(5000, 5000))
    assert queue.items[0].monitor_index == 0


def test_events_before_screen_is_ready_are_still_logged(queue, cursor):
    not_started = FakeScreenSource(monitors=[LEFT])
    InputHandler(queue, not_started, cursor).on_key("a", True)
    event = queue.items[0]
    assert event.monitor_index == 0 and event.monitor is None


@pytest.mark.parametrize("disabled, expected", [
    (["move"], ["mouse_down", "mouse_scroll", "key_press"]),
    (["click", "scroll"], ["mouse_move", "key_press"]),
    (["key"], ["mouse_move", "mouse_down", "mouse_scroll"]),
    ([], ["mouse_move", "mouse_down", "mouse_scroll", "key_press"]),
])
def test_disable(queue, screen, cursor, disabled, expected):
    handler = InputHandler(queue, screen, cursor, disable=disabled)
    handler.on_move()
    handler.on_click("Button.left", True)
    handler.on_scroll(0, 1)
    handler.on_key("a", True)
    assert [e.event_type.value for e in queue.items] == expected


def test_unknown_disable_name_is_ignored(queue, screen, cursor):
    handler = InputHandler(queue, screen, cursor, disable=["keyboard"])
    handler.on_key("a", True)
    assert len(queue.items) == 1


def test_on_event_hook_sees_every_event_and_cannot_break_capture(queue, screen, cursor):
    seen = []

    def hook(event):
        seen.append(event)
        raise RuntimeError("disk full")

    InputHandler(queue, screen, cursor, on_event=hook).on_key("a", True)
    assert len(seen) == 1 and queue.items == seen


def test_failing_cursor_source_degrades_to_unknown_position(queue, screen):
    class Broken(FakeCursor):
        def position(self):
            raise OSError("gone")

    InputHandler(queue, screen, Broken()).on_key("a", True)
    assert queue.items[0].cursor_position is None


# ---- MonitorLocator ----

def test_locator_refreshes_after_interval(screen):
    locator = MonitorLocator(screen, refresh_interval=0.0)
    assert locator.locate((700, 10)) == RIGHT
    screen._monitors = [LEFT]  # monitor unplugged
    assert locator.locate((700, 10)) == LEFT


# ---- ScreenshotLoop ----

def test_capture_grabs_monitor_under_cursor(queue, screen):
    cursor = FakeCursor((700, 10))
    image = ScreenshotLoop(queue, screen, cursor).capture_once()

    assert queue.items == [image]
    assert image.monitor_index == 1
    assert image.monitor_dict == RIGHT.to_dict()
    assert image.data.shape == (240, 320, 3)
    assert image.scale_factor == 1.0
    assert screen.grabs == {0: 0, 1: 1}


def test_capture_timestamp_is_taken_before_the_grab(queue, cursor, monkeypatch):
    clock = iter([50.0, 60.0])
    monkeypatch.setattr("capture.handlers.time.time", lambda: next(clock))

    class Slow(FakeScreenSource):
        def grab(self, monitor_index):
            import capture.handlers
            capture.handlers.time.time()  # time passes while grabbing
            return super().grab(monitor_index)

    source = Slow(monitors=[LEFT])
    source.start()
    assert ScreenshotLoop(queue, source, cursor).capture_once().timestamp == 50.0


def test_capture_returns_none_when_source_has_nothing(queue, cursor):
    not_started = FakeScreenSource(monitors=[LEFT])
    assert ScreenshotLoop(queue, not_started, cursor).capture_once() is None
    assert queue.items == []


def test_explicit_scale(queue, screen, cursor):
    image = ScreenshotLoop(queue, screen, cursor, scale=0.5).capture_once()
    assert image.data.shape == (180, 320, 3)
    assert image.scale_factor == 0.5


def test_per_monitor_scale_dict(queue, screen):
    loop = ScreenshotLoop(queue, screen, FakeCursor((700, 10)), scale={0: 0.5})
    assert loop.capture_once().scale_factor == 1.0  # monitor 1 has no entry


def test_target_dpi_scales_only_monitors_that_report_size(queue):
    hidpi = Monitor(index=0, left=0, top=0, width=800, height=400, width_mm=101.6)  # 200 dpi
    unknown = Monitor(index=1, left=800, top=0, width=800, height=400)
    source = FakeScreenSource(monitors=[hidpi, unknown])
    source.start()

    scaled = ScreenshotLoop(queue, source, FakeCursor((0, 0)), target_dpi=100).capture_once()
    assert scaled.data.shape == (200, 400, 3) and scaled.scale_factor == 0.5

    kept = ScreenshotLoop(queue, source, FakeCursor((900, 0)), target_dpi=100).capture_once()
    assert kept.data.shape == (400, 800, 3) and kept.scale_factor == 1.0


def test_scale_never_upscales(queue, screen, cursor):
    image = ScreenshotLoop(queue, screen, cursor, scale=2.0).capture_once()
    assert image.data.shape == (360, 640, 3)


def test_max_res_applies_when_no_scale(queue, screen, cursor):
    image = ScreenshotLoop(queue, screen, cursor, max_res=(320, 320)).capture_once()
    assert image.data.shape == (180, 320, 3)
    assert image.scale_factor == 0.5


def test_unchanged_frame_is_not_resized_again(queue, cursor, monkeypatch):
    source = FakeScreenSource(monitors=[LEFT], change_every=3)
    source.start()
    calls = []
    import capture.handlers as handlers
    real = handlers._resize_by_scale
    monkeypatch.setattr(handlers, "_resize_by_scale",
                        lambda data, scale: calls.append(1) or real(data, scale))

    loop = ScreenshotLoop(queue, source, cursor, scale=0.5)
    images = [loop.capture_once() for _ in range(6)]

    assert len(calls) == 2  # one per distinct source frame
    assert images[0].data is images[2].data
    assert images[0].data is not images[3].data


def test_loop_thread_fills_queue_and_stops(queue, screen, cursor):
    import time
    loop = ScreenshotLoop(queue, screen, cursor, fps=50)
    loop.start()
    time.sleep(0.2)
    loop.stop()
    count = len(queue.items)
    assert count >= 3
    time.sleep(0.1)
    assert len(queue.items) == count


def test_loop_survives_a_grab_that_raises(queue, cursor):
    import time

    class Flaky(ScreenSource):
        calls = 0

        def start(self): pass
        def stop(self): pass
        def monitors(self): return [LEFT]

        def grab(self, monitor_index):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("stream hiccup")
            return Frame(np.full((4, 4, 3), 9, dtype=np.uint8), LEFT)

    loop = ScreenshotLoop(queue, Flaky(), cursor, fps=50)
    loop.start()
    time.sleep(0.15)
    loop.stop()
    assert queue.items
