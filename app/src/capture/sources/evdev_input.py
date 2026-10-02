"""Global keyboard and mouse input on Linux, read from ``/dev/input``.

Works the same on Wayland and X11 because it sits below the display server.
Needs read access to the event devices: membership of the ``input`` group.
"""

from __future__ import annotations

import glob
import logging
import os
import select
import threading
import time
from typing import Optional

from capture.sources.base import BackendUnavailable, InputListener, InputSource
from capture.sources.evdev_keys import KeyTranslator, button_name

logger = logging.getLogger(__name__)

DEVICE_GLOB = "/dev/input/event*"

PERMISSION_HELP = (
    "Cannot read the input devices in /dev/input (permission denied).\n"
    "Add yourself to the input group once:\n"
    "    sudo usermod -aG input $USER\n"
    "then log out and back in. To use it before logging out, start the "
    "recorder with:\n"
    "    sg input -c 'uv run python -m juno'"
)


def readable_device_paths() -> tuple[list[str], list[str]]:
    """(readable, unreadable) event device paths."""
    paths = sorted(glob.glob(DEVICE_GLOB))
    readable = [p for p in paths if os.access(p, os.R_OK)]
    return readable, [p for p in paths if p not in readable]


def check_access() -> None:
    """Raise ``BackendUnavailable`` unless input devices can be read."""
    readable, unreadable = readable_device_paths()
    if not readable:
        if unreadable:
            raise BackendUnavailable(PERMISSION_HELP)
        raise BackendUnavailable("No input devices found in /dev/input.")


def _names(table: dict, code: int) -> list[str]:
    names = table.get(code, f"?{code}")
    return [names] if isinstance(names, str) else list(names)


class EvdevInputSource(InputSource):

    def __init__(self, rescan_interval: float = 5.0):
        self._rescan_interval = rescan_interval
        self._listener: Optional[InputListener] = None
        self._translator = KeyTranslator()
        self._devices: dict[int, object] = {}  # fd -> evdev.InputDevice
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ecodes = None

    # -- devices -----------------------------------------------------------

    def _open_new_devices(self) -> None:
        """Open every readable device with keys or buttons that is not open yet."""
        import evdev

        open_paths = {device.path for device in self._devices.values()}
        for path in readable_device_paths()[0]:
            if path in open_paths:
                continue
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            if evdev.ecodes.EV_KEY not in device.capabilities():
                device.close()
                continue
            self._devices[device.fd] = device
            logger.debug("reading input from %s (%s)", device.path, device.name)

    def _caps_lock_is_on(self) -> bool:
        e = self._ecodes
        for device in self._devices.values():
            try:
                if e.EV_LED in device.capabilities() and e.LED_CAPSL in device.leds():
                    return True
            except OSError:
                continue
        return False

    # -- events ------------------------------------------------------------

    def dispatch(self, ev_type: int, code: int, value: int, timestamp: float) -> None:
        """Translate one evdev event and pass it to the listener."""
        e = self._ecodes
        listener = self._listener
        if ev_type == e.EV_KEY:
            names = _names(e.bytype[e.EV_KEY], code)
            button = button_name(names)
            if button:
                if value in (0, 1):
                    listener.on_click(button, bool(value), timestamp=timestamp)
                return
            translated = self._translator.feed(names, value, code)
            if translated is not None:
                key, pressed = translated
                listener.on_key(key, pressed, timestamp=timestamp)
        elif ev_type == e.EV_REL:
            if code == e.REL_WHEEL:
                listener.on_scroll(0, value, timestamp=timestamp)
            elif code == e.REL_HWHEEL:
                listener.on_scroll(value, 0, timestamp=timestamp)

    def _read_loop(self) -> None:
        e = self._ecodes
        next_rescan = time.monotonic() + self._rescan_interval
        while not self._stop.is_set():
            if time.monotonic() >= next_rescan:
                self._open_new_devices()
                next_rescan = time.monotonic() + self._rescan_interval
            if not self._devices:
                self._stop.wait(0.5)
                continue
            try:
                readable, _, _ = select.select(list(self._devices), [], [], 0.5)
            except (OSError, ValueError):
                readable = []
            for fd in readable:
                device = self._devices.get(fd)
                if device is None:
                    continue
                try:
                    events = list(device.read())
                except OSError:  # unplugged
                    logger.debug("input device %s went away", device.path)
                    del self._devices[fd]
                    continue
                moved = None
                for event in events:
                    if event.type == e.EV_REL and event.code in (e.REL_X, e.REL_Y):
                        moved = event.timestamp()  # report motion once per batch
                        continue
                    try:
                        self.dispatch(event.type, event.code, event.value, event.timestamp())
                    except Exception:
                        logger.exception("input listener failed")
                if moved is not None:
                    try:
                        self._listener.on_move(timestamp=moved)
                    except Exception:
                        logger.exception("input listener failed")

    # -- InputSource -------------------------------------------------------

    def start(self, listener: InputListener) -> None:
        if self._thread is not None:
            return
        try:
            import evdev
        except ImportError:
            raise BackendUnavailable(
                "The evdev package is missing. Run: uv sync --extra wayland")
        check_access()
        self._ecodes = evdev.ecodes
        self._listener = listener
        self._open_new_devices()
        if not self._devices:
            raise BackendUnavailable("No keyboard or mouse found in /dev/input.")
        self._translator = KeyTranslator(caps_lock=self._caps_lock_is_on())
        logger.info("reading input from %d devices", len(self._devices))
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True,
                                        name="evdev-input")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        for device in self._devices.values():
            try:
                device.close()
            except OSError:
                pass
        self._devices.clear()
