import time

import pytest

from capture.sources import BACKENDS, BackendUnavailable, detect_backend_name, load_backend
from capture.sources.base import Backend, CursorSource, InputSource, Monitor, ScreenSource
from capture.sources.fake import FakeScreenSource, ScriptedInputSource


@pytest.mark.parametrize("env, expected", [
    ({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}, "wayland"),  # XWayland present
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, "x11"),
    ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland"),
    ({"DISPLAY": ":0"}, "x11"),
    ({"XDG_SESSION_TYPE": "Wayland"}, "wayland"),
])
def test_detect_backend_name(env, expected):
    assert detect_backend_name(env) == expected


def test_detect_fails_without_a_session():
    with pytest.raises(BackendUnavailable):
        detect_backend_name({"XDG_SESSION_TYPE": "tty"})


def test_unknown_backend():
    with pytest.raises(BackendUnavailable, match="Unknown backend"):
        load_backend("windows")


def test_unimplemented_backend_says_so():
    with pytest.raises(BackendUnavailable, match="not implemented"):
        load_backend("x11")


def test_wayland_backend_checks_input_access_before_anything_else(monkeypatch):
    from capture.sources import evdev_input
    monkeypatch.setattr(evdev_input, "readable_device_paths",
                        lambda: ([], ["/dev/input/event0"]))
    with pytest.raises(BackendUnavailable, match="input group"):
        load_backend("wayland")


def test_wayland_backend_is_portal_screen_plus_evdev_input(monkeypatch):
    from capture.sources import evdev_input, wayland
    from capture.sources.evdev_input import EvdevInputSource
    from capture.sources.portal_screen import PortalScreenSource
    monkeypatch.setattr(wayland, "check_access", lambda: None)
    backend = load_backend("wayland", fps=7)
    assert backend.name == "wayland"
    assert isinstance(backend.screen, PortalScreenSource)
    assert "7" == backend.screen._command[-1]
    assert isinstance(backend.input, EvdevInputSource)
    # The pointer position comes from the screen stream.
    assert backend.cursor is backend.screen and backend.cursor.position() is None


def test_every_registered_backend_module_exposes_create():
    import importlib
    for module_path in BACKENDS.values():
        assert callable(importlib.import_module(module_path).create)


def test_fake_backend_implements_the_interfaces():
    backend = load_backend("fake")
    assert isinstance(backend, Backend)
    assert isinstance(backend.screen, ScreenSource)
    assert isinstance(backend.input, InputSource)
    assert isinstance(backend.cursor, CursorSource)


def test_monitor_geometry():
    monitor = Monitor(index=1, left=1920, top=0, width=2560, height=1440, width_mm=597)
    assert monitor.contains(1920, 0) and monitor.contains(4479, 1439)
    assert not monitor.contains(1919, 0) and not monitor.contains(4480, 0)
    assert monitor.to_dict() == {"left": 1920, "top": 0, "width": 2560, "height": 1440}
    assert monitor.dpi == pytest.approx(108.9, abs=0.1)
    assert Monitor(0, 0, 0, 10, 10).dpi is None


def test_fake_screen_reuses_array_until_it_changes():
    source = FakeScreenSource(change_every=2)
    assert source.monitors() == [] and source.grab(0) is None  # not started
    source.start()
    a, b, c = (source.grab(0).data for _ in range(3))
    assert a is b and a is not c
    assert a.max() > 8  # never black
    assert source.grab(7) is None


def test_scripted_input_replays_and_stops():
    calls = []

    class Listener:
        def on_key(self, key, pressed, timestamp=None):
            calls.append((key, pressed))

    source = ScriptedInputSource(
        [(0.0, "on_key", ("a", True)), (0.01, "on_key", ("a", False))], loop=False)
    source.start(Listener())
    time.sleep(0.15)
    source.stop()
    source.stop()  # safe twice
    assert calls == [("a", True), ("a", False)]
