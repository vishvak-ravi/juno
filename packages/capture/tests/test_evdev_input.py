import pytest

evdev = pytest.importorskip("evdev")
e = evdev.ecodes

from capture.sources.wayland import evdev_input  # noqa: E402
from capture.sources.base import BackendUnavailable  # noqa: E402
from capture.sources.wayland.evdev_input import EvdevInputSource  # noqa: E402


class Listener:
    def __init__(self):
        self.calls = []

    def on_key(self, key, pressed, timestamp=None):
        self.calls.append(("key", key, pressed, timestamp))

    def on_click(self, button, pressed, pos=None, timestamp=None):
        self.calls.append(("click", button, pressed, timestamp))

    def on_scroll(self, dx, dy, pos=None, timestamp=None):
        self.calls.append(("scroll", dx, dy, timestamp))

    def on_move(self, pos=None, timestamp=None):
        self.calls.append(("move", timestamp))


@pytest.fixture
def wired():
    source = EvdevInputSource()
    source._ecodes = e
    source._listener = Listener()
    return source, source._listener


def test_ctrl_p_then_ssh_reaches_the_listener_with_kernel_timestamps(wired):
    source, listener = wired
    sequence = [(e.KEY_LEFTCTRL, 1), (e.KEY_P, 1), (e.KEY_P, 0), (e.KEY_LEFTCTRL, 0),
                (e.KEY_S, 1), (e.KEY_S, 0), (e.KEY_S, 1), (e.KEY_S, 0),
                (e.KEY_H, 1), (e.KEY_H, 0)]
    for i, (code, value) in enumerate(sequence):
        source.dispatch(e.EV_KEY, code, value, 100.0 + i)

    presses = [c[1] for c in listener.calls if c[0] == "key" and c[2]]
    assert presses == ["Key.ctrl", "p", "s", "s", "h"]
    assert listener.calls[0] == ("key", "Key.ctrl", True, 100.0)
    assert listener.calls[-1] == ("key", "h", False, 109.0)


def test_shift_state_carries_across_events(wired):
    source, listener = wired
    for code, value in [(e.KEY_LEFTSHIFT, 1), (e.KEY_1, 1), (e.KEY_LEFTSHIFT, 0), (e.KEY_1, 1)]:
        source.dispatch(e.EV_KEY, code, value, 1.0)
    assert [c[1] for c in listener.calls] == ["Key.shift", "!", "Key.shift", "1"]


def test_mouse_buttons_and_wheel(wired):
    source, listener = wired
    source.dispatch(e.EV_KEY, e.BTN_LEFT, 1, 5.0)
    source.dispatch(e.EV_KEY, e.BTN_LEFT, 0, 5.1)
    source.dispatch(e.EV_KEY, e.BTN_RIGHT, 1, 5.2)
    source.dispatch(e.EV_REL, e.REL_WHEEL, -1, 5.3)
    source.dispatch(e.EV_REL, e.REL_HWHEEL, 2, 5.4)
    assert listener.calls == [
        ("click", "Button.left", True, 5.0), ("click", "Button.left", False, 5.1),
        ("click", "Button.right", True, 5.2),
        ("scroll", 0, -1, 5.3), ("scroll", 2, 0, 5.4)]


def test_ignored_events(wired):
    source, listener = wired
    source.dispatch(e.EV_KEY, e.BTN_LEFT, 2, 1.0)        # buttons do not repeat
    source.dispatch(e.EV_KEY, e.KEY_LEFTCTRL, 2, 1.0)    # held modifier
    source.dispatch(e.EV_REL, e.REL_WHEEL_HI_RES, 120, 1.0)
    source.dispatch(e.EV_SYN, 0, 0, 1.0)
    source.dispatch(e.EV_MSC, e.MSC_SCAN, 458756, 1.0)
    assert listener.calls == []


def test_no_readable_devices_explains_the_input_group(monkeypatch):
    monkeypatch.setattr(evdev_input, "readable_device_paths",
                        lambda: ([], ["/dev/input/event0"]))
    with pytest.raises(BackendUnavailable, match="usermod -aG input"):
        EvdevInputSource().start(Listener())


def test_no_devices_at_all(monkeypatch):
    monkeypatch.setattr(evdev_input, "readable_device_paths", lambda: ([], []))
    with pytest.raises(BackendUnavailable, match="No input devices"):
        evdev_input.check_access()


def test_stop_before_start_is_safe():
    EvdevInputSource().stop()
