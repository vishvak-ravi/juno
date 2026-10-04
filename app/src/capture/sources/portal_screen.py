"""Screen frames on Wayland, via xdg-desktop-portal ScreenCast and PipeWire.

The portal and PipeWire work happens in ``portal_helper.py`` under the system
Python, which has the GStreamer bindings. This class starts that helper, reads
frames from its stdout, and keeps the newest one per monitor. The stream also
carries the pointer position, so this class is the Wayland ``CursorSource`` too;
the pointer itself is not drawn into the frames.

The first run shows the desktop's share dialog once. After that the stored
restore token lets the session start silently. The desktop shows its "screen is
being shared" indicator for as long as the recorder runs.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import BinaryIO, Optional, Sequence

import numpy as np

from capture.sources.base import (
    BackendUnavailable,
    CursorSource,
    Frame,
    Monitor,
    Position,
    ScreenSource,
)

logger = logging.getLogger(__name__)

HELPER = Path(__file__).with_name("portal_helper.py")
SYSTEM_PYTHON = "/usr/bin/python3"
RESTART_DELAY_S = 5.0


def default_token_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "juno" / "portal-token"


def _read_exact(stream: BinaryIO, size: int) -> Optional[bytearray]:
    """Read exactly ``size`` bytes, or None at end of stream."""
    buffer = bytearray(size)
    view = memoryview(buffer)
    got = 0
    while got < size:
        n = stream.readinto(view[got:])
        if not n:
            return None
        got += n
    return buffer


def read_message(stream: BinaryIO) -> Optional[tuple[dict, Optional[bytearray]]]:
    """One helper message as (header, payload), or None at end of stream."""
    prefix = _read_exact(stream, 4)
    if prefix is None:
        return None
    raw = _read_exact(stream, struct.unpack("<I", prefix)[0])
    if raw is None:
        return None
    header = json.loads(raw)
    payload = None
    if "size" in header:
        payload = _read_exact(stream, header["size"])
        if payload is None:
            return None
    return header, payload


def frame_array(header: dict, payload: bytearray) -> np.ndarray:
    """The H x W x 3 RGB array for a frame message, without copying when possible."""
    width, height, stride = header["width"], header["height"], header["stride"]
    rows = np.frombuffer(payload, dtype=np.uint8)[: stride * height].reshape(height, stride)
    if stride == width * 3:
        return rows.reshape(height, width, 3)
    return np.ascontiguousarray(rows[:, : width * 3]).reshape(height, width, 3)


class PortalScreenSource(ScreenSource, CursorSource):

    def __init__(
        self,
        fps: int = 5,
        token_file: Optional[Path] = None,
        start_timeout: float = 180.0,
        command: Optional[Sequence[str]] = None,
    ):
        """
        Args:
            fps: cap on frames per second requested from the helper.
            token_file: where the portal restore token is kept.
            start_timeout: how long ``start`` waits for the user to approve.
            command: helper command line, replaceable for tests.
        """
        self._token_file = Path(token_file) if token_file else default_token_file()
        self._start_timeout = start_timeout
        self._command = list(command) if command else [
            SYSTEM_PYTHON, str(HELPER),
            "--token-file", str(self._token_file), "--fps", str(fps)]
        self._lock = threading.Lock()
        self._monitors: list[Monitor] = []
        self._frames: dict[int, np.ndarray] = {}
        # (monitor index, x, y) in that stream's pixels, as last reported.
        self._cursor: Optional[tuple[int, int, int]] = None
        self._error: Optional[str] = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    # -- helper process ----------------------------------------------------

    def _spawn(self) -> subprocess.Popen:
        try:
            return subprocess.Popen(
                self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None,  # helper logs go straight to our stderr
            )
        except OSError as exc:
            raise BackendUnavailable(f"could not start the screen capture helper: {exc}")

    def _consume(self, process: subprocess.Popen) -> None:
        """Read helper messages until it exits."""
        while True:
            try:
                message = read_message(process.stdout)
            except (OSError, ValueError) as exc:
                logger.error("bad data from the screen capture helper: %s", exc)
                return
            if message is None:
                return
            header, payload = message
            kind = header.get("type")
            if kind == "frame":
                array = frame_array(header, payload)
                with self._lock:
                    self._frames[header["index"]] = array
            elif kind == "cursor":
                with self._lock:
                    self._cursor = (header["index"], header["x"], header["y"])
            elif kind == "streams":
                with self._lock:
                    self._monitors = [
                        Monitor(index=s["index"], left=s["left"], top=s["top"],
                                width=s["width"], height=s["height"],
                                width_mm=s.get("width_mm"))
                        for s in header["streams"]]
                    self._error = None
                self._ready.set()
            elif kind == "status":
                if header.get("state") == "awaiting_consent" and not self._token_file.exists():
                    print("Waiting for you to approve screen sharing in the desktop "
                          "dialog (first run only).", file=sys.stderr)
            elif kind == "error":
                with self._lock:
                    self._error = header.get("message", "unknown error")
                logger.error("screen capture helper: %s", self._error)

    def _run(self) -> None:
        """Keep a helper alive until stopped; a dropped session is restarted."""
        while not self._stopping.is_set():
            process = self._process
            self._consume(process)
            process.wait()
            with self._lock:
                self._frames.clear()
                self._monitors = []
                self._cursor = None
                if self._error is None and not self._stopping.is_set():
                    self._error = f"the screen capture helper exited ({process.returncode})"
            self._ready.set()  # wake start() if it is still waiting
            if self._stopping.wait(RESTART_DELAY_S):
                return
            logger.warning("restarting screen capture")
            try:
                self._process = self._spawn()
            except BackendUnavailable as exc:
                logger.error("%s", exc)
                return

    # -- ScreenSource ------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._ready.clear()
        self._error = None
        self._process = self._spawn()
        self._thread = threading.Thread(target=self._run, daemon=True, name="portal-screen")
        self._thread.start()

        approved = self._ready.wait(self._start_timeout)
        with self._lock:
            ready, error = bool(self._monitors), self._error
        if not ready:
            self.stop()
            if not approved:
                raise BackendUnavailable(
                    f"screen sharing was not approved within {self._start_timeout:.0f} s")
            raise BackendUnavailable(f"screen capture failed: {error}")

    def stop(self) -> None:
        self._stopping.set()
        process, self._process = self._process, None
        if process is not None:
            try:
                process.stdin.close()  # the helper exits on stdin EOF
                process.wait(timeout=3.0)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        with self._lock:
            self._frames.clear()
            self._monitors = []
            self._cursor = None

    def monitors(self) -> list[Monitor]:
        with self._lock:
            return list(self._monitors)

    def grab(self, monitor_index: int) -> Optional[Frame]:
        with self._lock:
            data = self._frames.get(monitor_index)
            monitor = next((m for m in self._monitors if m.index == monitor_index), None)
        if data is None or monitor is None:
            return None
        return Frame(data=data, monitor=monitor)

    # -- CursorSource ------------------------------------------------------

    def position(self) -> Optional[Position]:
        with self._lock:
            if self._cursor is None:
                return None
            index, x, y = self._cursor
            data = self._frames.get(index)
            monitor = next((m for m in self._monitors if m.index == index), None)
        if data is None or monitor is None:
            return None
        # Stream pixels to desktop coordinates: they differ under display scaling.
        return (monitor.left + round(x * monitor.width / data.shape[1]),
                monitor.top + round(y * monitor.height / data.shape[0]))
