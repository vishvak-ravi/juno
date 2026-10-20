import inspect

import numpy as np
import pytest

# Importing pynput needs an X display on Linux.
pytest.importorskip("pynput", exc_type=ImportError)

from capture.sources import load_backend, macos  # noqa: E402
from capture.sources import pynput_mss  # noqa: E402
from capture.sources.base import BackendUnavailable, Monitor  # noqa: E402
from capture.sources.pynput_mss import (  # noqa: E402
    MssScreen,
    PynputCursor,
    PynputInput,
    key_name,
    to_monitors,
)


class FakeListener:
    """Stands in for pynput's listeners: keeps the callbacks for the test to fire."""

    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class Recorder:
    def __init__(self):
        self.calls = []

    def on_move(self, pos=None, timestamp=None):
        self.calls.append(("move", pos))

    def on_click(self, button, pressed, pos=None, timestamp=None):
        self.calls.append(("click", button, pressed, pos))

    def on_scroll(self, dx, dy, pos=None, timestamp=None):
        self.calls.append(("scroll", dx, dy, pos))

    def on_key(self, key, pressed, timestamp=None):
        self.calls.append(("key", key, pressed))


@pytest.fixture
def listeners(monkeypatch):
    made = {}

    def factory(kind):
        def make(**callbacks):
            made[kind] = FakeListener(**callbacks)
            return made[kind]
        return make

    monkeypatch.setattr(pynput_mss.mouse, "Listener", factory("mouse"))
    monkeypatch.setattr(pynput_mss.keyboard, "Listener", factory("keyboard"))
    return made


def test_input_events_reach_the_listener_in_the_recorder_conventions(listeners):
    keyboard = pynput_mss.keyboard
    recorder = Recorder()
    source = PynputInput()
    source.start(recorder)
    mouse_cb = listeners["mouse"].callbacks
    key_cb = listeners["keyboard"].callbacks
    assert listeners["mouse"].started and listeners["keyboard"].started

    mouse_cb["on_move"](10.5, 20.7)
    mouse_cb["on_click"](3.2, 4.9, pynput_mss.mouse.Button.left, True)
    mouse_cb["on_scroll"](5, 6, 0, -1)
    key_cb["on_press"](keyboard.KeyCode.from_char("a"))
    key_cb["on_release"](keyboard.Key.enter)

    assert recorder.calls == [
        ("move", (10, 20)),
        ("click", "Button.left", True, (3, 4)),
        ("scroll", 0, -1, (5, 6)),
        ("key", "a", True),
        ("key", "Key.enter", False),
    ]
    source.stop()
    source.stop()  # safe twice
    assert listeners["mouse"].stopped and listeners["keyboard"].stopped


def test_callbacks_take_exactly_pynputs_arguments(listeners):
    # pynput passes a *args callback an extra ``injected`` argument.
    PynputInput().start(Recorder())
    for listener in listeners.values():
        for callback in listener.callbacks.values():
            assert inspect.getfullargspec(callback).varargs is None


def test_a_failing_listener_does_not_stop_pynput(listeners):
    class Broken(Recorder):
        def on_key(self, key, pressed, timestamp=None):
            raise RuntimeError("boom")

    PynputInput().start(Broken())
    listeners["keyboard"].callbacks["on_press"](pynput_mss.keyboard.Key.shift)  # no raise


def test_key_names():
    keyboard = pynput_mss.keyboard
    assert key_name(keyboard.KeyCode.from_char("A")) == "A"
    assert key_name(keyboard.Key.cmd) == "Key.cmd"
    # A key pynput has no character for is still named, never null.
    assert key_name(keyboard.KeyCode(vk=63)) == "<63>"


def test_monitors_skip_the_combined_entry_and_take_physical_widths():
    mss_monitors = [
        {"left": 0, "top": 0, "width": 4480, "height": 1440},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 2560, "height": 1440},
    ]
    monitors = to_monitors(mss_monitors, {(1920, 0, 2560, 1440): 597})
    assert monitors == [
        Monitor(0, 0, 0, 1920, 1080, width_mm=None),
        Monitor(1, 1920, 0, 2560, 1440, width_mm=597),
    ]


class FakeMss:
    monitors = [{"left": 0, "top": 0, "width": 4, "height": 2},
                {"left": 0, "top": 0, "width": 4, "height": 2}]
    closed = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        FakeMss.closed += 1

    def grab(self, monitor):
        bgra = np.zeros((monitor["height"], monitor["width"], 4), dtype=np.uint8)
        bgra[..., 0], bgra[..., 1], bgra[..., 2] = 30, 20, 10  # B, G, R
        return bgra


def test_screen_grabs_rgb_frames_of_a_known_monitor(monkeypatch):
    monkeypatch.setattr(pynput_mss.mss, "MSS", FakeMss)
    monkeypatch.setattr(pynput_mss, "width_mm_by_geometry", lambda: {})
    screen = MssScreen()
    assert screen.monitors() == [] and screen.grab(0) is None  # not started
    screen.start()
    assert screen.monitors() == [Monitor(0, 0, 0, 4, 2)]
    frame = screen.grab(0)
    assert frame.data.shape == (2, 4, 3)
    assert tuple(frame.data[0, 0]) == (10, 20, 30)
    assert screen.grab(1) is None
    FakeMss.closed = 0
    screen.stop()
    screen.stop()  # safe twice
    assert FakeMss.closed == 1 and screen.monitors() == []


def test_cursor_reports_integer_positions(monkeypatch):
    class Controller:
        position = (12.7, 3.2)

    monkeypatch.setattr(pynput_mss.mouse, "Controller", Controller)
    assert PynputCursor().position() == (12, 3)


def test_macos_refuses_to_start_without_permissions(monkeypatch):
    requested = []
    monkeypatch.setattr(macos, "missing_permissions", lambda: ["Screen Recording"])
    monkeypatch.setattr(macos, "request_access", lambda: requested.append(True))
    with pytest.raises(BackendUnavailable, match="Screen Recording"):
        load_backend("macos")
    assert requested


@pytest.mark.parametrize("name", ["macos", "x11"])
def test_backends_are_mss_screen_plus_pynput_input(monkeypatch, name):
    monkeypatch.setattr(macos, "missing_permissions", lambda: [])
    backend = load_backend(name)
    assert backend.name == name
    assert isinstance(backend.screen, MssScreen)
    assert isinstance(backend.input, PynputInput)
    assert isinstance(backend.cursor, PynputCursor)
