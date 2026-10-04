import io
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from capture.sources import portal_screen
from capture.sources.base import BackendUnavailable
from capture.sources.portal_screen import PortalScreenSource, frame_array, read_message

FAKE = str(Path(__file__).with_name("fake_helper.py"))


def source(mode="ok", tmp_path=None, **kwargs):
    kwargs.setdefault("start_timeout", 10.0)
    return PortalScreenSource(command=[sys.executable, FAKE, mode],
                              token_file=(tmp_path or Path("/nonexistent")) / "token",
                              **kwargs)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def message(header, payload=b""):
    if payload:
        header = {**header, "size": len(payload)}
    raw = json.dumps(header).encode()
    return struct.pack("<I", len(raw)) + raw + payload


def test_read_message_parses_header_and_payload():
    stream = io.BytesIO(message({"type": "frame", "index": 0}, b"abc") + message({"type": "x"}))
    assert read_message(stream) == ({"type": "frame", "index": 0, "size": 3}, bytearray(b"abc"))
    assert read_message(stream) == ({"type": "x"}, None)
    assert read_message(stream) is None


@pytest.mark.parametrize("cut", [2, 10, -1])
def test_read_message_returns_none_on_truncated_stream(cut):
    data = message({"type": "frame"}, b"abcdef")
    assert read_message(io.BytesIO(data[:cut])) is None


def test_frame_array_is_zero_copy_when_rows_are_tight():
    payload = bytearray(range(24))
    array = frame_array({"width": 4, "height": 2, "stride": 12}, payload)
    assert array.shape == (2, 4, 3)
    assert array[1, 0].tolist() == [12, 13, 14]
    assert np.shares_memory(array, np.frombuffer(payload, dtype=np.uint8))


def test_frame_array_strips_row_padding():
    row = bytes([1, 2, 3] * 5) + b"\xff"
    array = frame_array({"width": 5, "height": 2, "stride": 16}, bytearray(row * 2))
    assert array.shape == (2, 5, 3)
    assert (array == [1, 2, 3]).all()
    assert array.flags["C_CONTIGUOUS"]


def test_default_token_file_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert portal_screen.default_token_file() == tmp_path / "juno" / "portal-token"


def test_start_delivers_monitors_and_latest_frames(tmp_path):
    screen = source(tmp_path=tmp_path)
    assert screen.monitors() == [] and screen.grab(0) is None
    screen.start()
    try:
        monitors = screen.monitors()
        assert [(m.index, m.left, m.width, m.height, m.width_mm) for m in monitors] == [
            (0, 0, 8, 4, 100), (1, 8, 5, 2, None)]
        # The newest frame for monitor 0 is the second one sent.
        assert wait_for(lambda: screen.grab(0) is not None and screen.grab(0).data[0, 0, 0] == 9)
        first = screen.grab(0)
        assert first.data.shape == (4, 8, 3) and first.monitor == monitors[0]
        assert screen.grab(0).data is first.data  # same array until a new frame arrives
        assert wait_for(lambda: screen.grab(1) is not None)
        assert screen.grab(1).data.shape == (2, 5, 3)
        assert screen.grab(5) is None
        # The helper reported the pointer at (4, 1) on monitor 1, which starts at x=8.
        assert wait_for(lambda: screen.position() == (12, 1))
    finally:
        screen.stop()
    assert screen.monitors() == [] and screen.grab(0) is None
    assert screen.position() is None
    screen.stop()  # safe twice


def test_helper_exits_when_the_source_stops(tmp_path):
    screen = source(tmp_path=tmp_path)
    screen.start()
    process = screen._process
    screen.stop()
    assert process.poll() is not None


def test_declined_dialog_raises_with_the_reason(tmp_path):
    with pytest.raises(BackendUnavailable, match="declined"):
        source("denied", tmp_path).start()


def test_no_answer_times_out_and_kills_the_helper(tmp_path):
    screen = source("silent", tmp_path, start_timeout=0.3)
    with pytest.raises(BackendUnavailable, match="not approved within"):
        screen.start()
    assert screen._process is None


def test_missing_helper_interpreter(tmp_path):
    screen = PortalScreenSource(command=["/nonexistent/python"], token_file=tmp_path / "t")
    with pytest.raises(BackendUnavailable, match="could not start"):
        screen.start()


def test_a_dropped_session_is_restarted(tmp_path, monkeypatch):
    monkeypatch.setattr(portal_screen, "RESTART_DELAY_S", 0.1)
    screen = source("crash", tmp_path)
    screen.start()
    try:
        first = screen._process
        # The helper dies after two frames; frames must not go stale...
        assert wait_for(lambda: screen._process is not first and screen._process is not None)
        # ...and a new helper brings the monitors back.
        assert wait_for(lambda: len(screen.monitors()) == 2)
    finally:
        screen.stop()


def test_first_run_tells_the_user_to_look_for_the_dialog(tmp_path, capsys):
    screen = source(tmp_path=tmp_path)
    screen.start()
    screen.stop()
    assert "approve screen sharing" in capsys.readouterr().err


def test_no_dialog_hint_once_a_token_exists(tmp_path, capsys):
    (tmp_path / "token").write_text("abc")
    screen = source(tmp_path=tmp_path)
    screen.start()
    screen.stop()
    assert "approve screen sharing" not in capsys.readouterr().err
