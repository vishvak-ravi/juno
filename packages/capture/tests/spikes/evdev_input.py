#!/usr/bin/env python
"""Spike 3: does evdev see my real keyboard and mouse on Wayland?

Reads /dev/input directly and prints every key, click and scroll next to the
name the recorder would log for it (pynput's names, which is what Tada logs).
Type something known, then compare it with the summary line at the end.

    uv sync
    sudo .venv/bin/python packages/capture/tests/spikes/evdev_input.py            # works right now
    .venv/bin/python packages/capture/tests/spikes/evdev_input.py                 # once in the input group

To join the group (then log out and back in):
    sudo usermod -aG input $USER
"""

import sys

sys.dont_write_bytecode = True  # may run as root; leave no root-owned caches behind

import argparse
import glob
import os
import select
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from capture.sources.wayland.evdev_keys import PRESS, REPEAT, KeyTranslator, button_name  # noqa: E402


def _names(table, code):
    names = table.get(code, f"?{code}")
    return [names] if isinstance(names, str) else list(names)


class Session:
    """Turns raw evdev events into the lines and totals this spike reports."""

    def __init__(self, ecodes, caps_lock=False, out=print):
        self.ecodes = ecodes
        self.translator = KeyTranslator(caps_lock=caps_lock)
        self.out = out
        self.started = time.time()
        self.presses = []
        self.repeats = 0
        self.clicks = 0
        self.scrolls = 0
        self.motion = 0
        self.devices = {}

    def _line(self, timestamp, kind, logged, raw, device):
        self.devices[device] = self.devices.get(device, 0) + 1
        self.out(f"  {timestamp - self.started:7.3f}s  {kind:<13} {logged:<16} {raw:<18} [{device}]")

    def handle(self, ev_type, code, value, timestamp, device):
        e = self.ecodes
        if ev_type == e.EV_KEY:
            names = _names(e.bytype[e.EV_KEY], code)
            button = button_name(names)
            if button:
                if value in (0, 1):
                    self.clicks += value
                    self._line(timestamp, "mouse_down" if value else "mouse_up",
                               button, names[0], device)
                return
            result = self.translator.feed(names, value, code)
            if result is None:
                return
            name, pressed = result
            if value == PRESS:
                self.presses.append(name)
            elif value == REPEAT:
                self.repeats += 1
            kind = "key_press" if pressed else "key_release"
            self._line(timestamp, kind + (" (repeat)" if value == REPEAT else ""),
                       repr(name) if len(name) == 1 else name, names[0], device)
        elif ev_type == e.EV_REL:
            if code == e.REL_WHEEL:
                self.scrolls += 1
                self._line(timestamp, "mouse_scroll", f"dx=0 dy={value}", "REL_WHEEL", device)
            elif code == e.REL_HWHEEL:
                self.scrolls += 1
                self._line(timestamp, "mouse_scroll", f"dx={value} dy=0", "REL_HWHEEL", device)
            elif code in (e.REL_X, e.REL_Y):
                self.motion += 1

    def summary(self):
        lines = ["", "Key presses the recorder would log, in order:"]
        lines.append("  " + (" ".join(self.presses) if self.presses else "(none)"))
        if self.repeats:
            lines.append(f"  plus {self.repeats} auto-repeat presses from held keys")
        lines.append(f"Clicks: {self.clicks}   Scroll events: {self.scrolls}   "
                     f"Pointer motion events: {self.motion} (seen, not logged: Tada drops moves)")
        if self.devices:
            lines.append("Devices that produced events: "
                         + ", ".join(f"{name} ({count})" for name, count in self.devices.items()))
        if not (self.presses or self.clicks or self.scrolls or self.motion):
            lines.append("NOTHING WAS SEEN. evdev is not receiving input from these devices.")
        return "\n".join(lines)


def open_devices(evdev):
    """Every readable device that has keys or buttons, plus the unreadable paths."""
    devices, denied = [], []
    for path in sorted(glob.glob("/dev/input/event*")):
        if not os.access(path, os.R_OK):
            denied.append(path)
            continue
        device = evdev.InputDevice(path)
        if evdev.ecodes.EV_KEY in device.capabilities():
            devices.append(device)
        else:
            device.close()
    return devices, denied


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration", type=float, default=15.0,
                        help="Seconds to listen (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        import evdev
    except ImportError:
        print("evdev is not installed. Run: uv sync", file=sys.stderr)
        return 2

    devices, denied = open_devices(evdev)
    if not devices:
        print(f"Cannot read any input device ({len(denied)} present, permission denied).\n"
              "Run this once as root:\n"
              f"    sudo {sys.executable} {Path(__file__).resolve()}\n"
              "or join the input group, then log out and back in:\n"
              "    sudo usermod -aG input $USER", file=sys.stderr)
        return 2

    caps_lock = any(evdev.ecodes.LED_CAPSL in d.leds() for d in devices
                    if evdev.ecodes.EV_LED in d.capabilities())
    print(f"Reading {len(devices)} devices:")
    for device in devices:
        print(f"  {device.path}  {device.name}")
    print(f"\nListening for {args.duration:g} s. Type, click and scroll now, in any window.\n")
    print(f"  {'time':>8}  {'event':<13} {'logged as':<16} {'evdev code':<18} device")

    session = Session(evdev.ecodes, caps_lock=caps_lock)
    by_fd = {device.fd: device for device in devices}
    deadline = time.monotonic() + args.duration
    try:
        while by_fd and (remaining := deadline - time.monotonic()) > 0:
            readable, _, _ = select.select(list(by_fd), [], [], min(remaining, 0.5))
            for fd in readable:
                device = by_fd[fd]
                try:
                    events = list(device.read())
                except OSError:  # unplugged
                    del by_fd[fd]
                    continue
                for event in events:
                    session.handle(event.type, event.code, event.value,
                                   event.timestamp(), device.name)
    except KeyboardInterrupt:
        print("\n(stopped with Ctrl+C; that Ctrl+C is in the log above)")

    print(session.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
