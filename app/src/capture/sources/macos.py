"""macOS backend: napsack's own path, mss frames and pynput input and pointer.

macOS grants the three permissions this needs (Privacy & Security in System
Settings) to the app that started the process, such as the terminal, not to
Python. Without Screen Recording, frames still arrive but show only the
wallpaper and menu bar, which ``capture.check`` cannot tell from a real frame,
so ``create`` refuses to start until all three are granted.
"""

from __future__ import annotations

import ctypes
import ctypes.util

from capture.sources.base import Backend, BackendUnavailable

# IOHIDCheckAccess / IOHIDRequestAccess arguments and result (IOKit/hid/IOHIDLib.h).
HID_REQUEST_LISTEN_EVENT = 1
HID_ACCESS_GRANTED = 0


def framework(name: str) -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library(name))


def screen_recording_granted() -> bool:
    graphics = framework("CoreGraphics")
    graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    return graphics.CGPreflightScreenCaptureAccess()


def accessibility_granted() -> bool:
    services = framework("ApplicationServices")
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    return services.AXIsProcessTrusted()


def input_monitoring_granted() -> bool:
    iokit = framework("IOKit")
    iokit.IOHIDCheckAccess.restype = ctypes.c_int
    iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint]
    return iokit.IOHIDCheckAccess(HID_REQUEST_LISTEN_EVENT) == HID_ACCESS_GRANTED


def request_access() -> None:
    """Add the app to the Screen Recording and Input Monitoring lists, prompting once.

    The user still has to switch it on there and restart the app.
    """
    graphics = framework("CoreGraphics")
    graphics.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    graphics.CGRequestScreenCaptureAccess()
    iokit = framework("IOKit")
    iokit.IOHIDRequestAccess.restype = ctypes.c_bool
    iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint]
    iokit.IOHIDRequestAccess(HID_REQUEST_LISTEN_EVENT)


def missing_permissions() -> list[str]:
    checks = (("Screen Recording", screen_recording_granted),
              ("Accessibility", accessibility_granted),
              ("Input Monitoring", input_monitoring_granted))
    return [name for name, granted in checks if not granted()]


def create(**options) -> Backend:
    missing = missing_permissions()
    if missing:
        request_access()
        raise BackendUnavailable(
            f"macOS has not granted {', '.join(missing)} to the app that started "
            "this process (your terminal, if you ran it from one). Turn it on in "
            "System Settings > Privacy & Security, quit and reopen that app, and "
            "run again."
        )
    # Imported here so that importing this module does not load pynput.
    from capture.sources.pynput_mss import MssScreen, PynputCursor, PynputInput

    return Backend(name="macos", screen=MssScreen(), input=PynputInput(), cursor=PynputCursor())
