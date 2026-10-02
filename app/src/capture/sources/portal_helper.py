#!/usr/bin/python3
"""Screen frames from xdg-desktop-portal ScreenCast, written to stdout.

Runs under the SYSTEM Python (``/usr/bin/python3``), not the project venv:
it needs PyGObject with GStreamer and PipeWire, which the distro ships and pip
does not. ``capture.sources.portal_screen`` starts it and reads its output, so
this file must not import anything from the ``capture`` package.

Protocol on stdout, one message after another:
    4 bytes   little-endian length of the JSON header
    N bytes   JSON header, always with a "type"
    M bytes   payload, present when the header has "size"

    {"type": "status", "state": "awaiting_consent"}
    {"type": "streams", "streams": [{"index", "left", "top", "width", "height", "width_mm"}]}
    {"type": "frame", "index", "width", "height", "stride", "size"}   + RGB bytes
    {"type": "error", "message"}

It exits when stdin closes, so it never outlives the recorder.
"""

import argparse
import itertools
import json
import os
import struct
import sys
import threading

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"

SOURCE_MONITOR = 1
CURSOR_EMBEDDED = 2   # drawn into the frame, like mss(with_cursor=True) in Tada
PERSIST_UNTIL_REVOKED = 2

_out = sys.stdout.buffer
_out_lock = threading.Lock()


def log(message):
    print(f"portal_helper: {message}", file=sys.stderr, flush=True)


def send(header, payload=None):
    if payload is not None:
        header["size"] = len(payload)
    raw = json.dumps(header).encode()
    with _out_lock:
        _out.write(struct.pack("<I", len(raw)))
        _out.write(raw)
        if payload is not None:
            _out.write(payload)
        _out.flush()


class PortalError(RuntimeError):
    pass


class Portal:
    """The request/response dance every ScreenCast method uses."""

    def __init__(self, bus):
        self.bus = bus
        self.sender = bus.get_unique_name()[1:].replace(".", "_")
        self._tokens = (f"juno{n}" for n in itertools.count(1))

    def request(self, method, build_params, timeout_s=None):
        """Call ``method`` and wait for its Response signal. Returns the results dict."""
        token = next(self._tokens)
        path = f"{PORTAL_PATH}/request/{self.sender}/{token}"
        loop = GLib.MainLoop()
        outcome = {}

        def on_response(_bus, _sender, _path, _iface, _signal, params):
            outcome["code"], outcome["results"] = params.unpack()
            loop.quit()

        subscription = self.bus.signal_subscribe(
            PORTAL_NAME, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, on_response)
        timer = GLib.timeout_add_seconds(timeout_s, loop.quit) if timeout_s else None
        try:
            self.bus.call_sync(
                PORTAL_NAME, PORTAL_PATH, SCREENCAST, method, build_params(token),
                GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None)
            loop.run()
        finally:
            self.bus.signal_unsubscribe(subscription)
            if timer and "code" in outcome:
                GLib.source_remove(timer)

        if "code" not in outcome:
            raise PortalError(f"{method}: no response from the portal")
        if outcome["code"] == 1:
            raise PortalError(f"{method}: screen sharing was declined")
        if outcome["code"] != 0:
            raise PortalError(f"{method}: portal returned error code {outcome['code']}")
        return outcome["results"]

    def open_pipewire_remote(self, session):
        result, fd_list = self.bus.call_with_unix_fd_list_sync(
            PORTAL_NAME, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (session, {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, -1, None, None)
        return fd_list.get(result.unpack()[0])


def read_token(path):
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def write_token(path, token):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.replace(tmp, path)


def physical_widths():
    """{(left, top): width_mm} for each monitor, or {} if GDK cannot tell us."""
    try:
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
        Gtk.init_check()
        monitors = Gdk.Display.get_default().get_monitors()
        widths = {}
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            geometry = monitor.get_geometry()
            if monitor.get_width_mm() > 0:
                widths[(geometry.x, geometry.y)] = monitor.get_width_mm()
        return widths
    except Exception as exc:  # optional: only used to scale screenshots by DPI
        log(f"could not read physical monitor sizes: {exc}")
        return {}


def negotiate(portal, token_file):
    """Run the portal handshake. Returns (session handle, [(node_id, properties)])."""
    variant = GLib.Variant

    session = portal.request("CreateSession", lambda token: variant("(a{sv})", ({
        "handle_token": variant("s", token),
        "session_handle_token": variant("s", "juno"),
    },)), timeout_s=30)["session_handle"]

    options = {
        "types": variant("u", SOURCE_MONITOR),
        "multiple": variant("b", True),
        "cursor_mode": variant("u", CURSOR_EMBEDDED),
        "persist_mode": variant("u", PERSIST_UNTIL_REVOKED),
    }
    restore_token = read_token(token_file)
    if restore_token:
        options["restore_token"] = variant("s", restore_token)
    portal.request("SelectSources", lambda token: variant("(oa{sv})", (
        session, {**options, "handle_token": variant("s", token)})), timeout_s=30)

    # With a valid restore token this returns at once. Otherwise the desktop
    # shows its share dialog and this waits for the user.
    send({"type": "status", "state": "awaiting_consent"})
    results = portal.request("Start", lambda token: variant("(osa{sv})", (
        session, "", {"handle_token": variant("s", token)})))

    new_token = results.get("restore_token")
    if new_token:
        write_token(token_file, new_token)  # single use: each Start issues a new one
    else:
        log("the portal returned no restore token; the dialog will show again next run")
    return session, list(results.get("streams", []))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--fps", type=int, default=5)
    args = parser.parse_args()

    Gst.init(None)
    loop = GLib.MainLoop()
    exit_code = [0]

    def fail(message, code=3):
        log(message)
        try:
            send({"type": "error", "message": message})
        except OSError:
            pass
        exit_code[0] = code
        loop.quit()

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        portal = Portal(bus)
        session, streams = negotiate(portal, args.token_file)
    except (PortalError, GLib.Error) as exc:
        message = str(exc)
        log(message)
        send({"type": "error", "message": message})
        return 2
    if not streams:
        send({"type": "error", "message": "the portal granted no monitors"})
        return 2

    bus.signal_subscribe(
        PORTAL_NAME, "org.freedesktop.portal.Session", "Closed", session, None,
        Gio.DBusSignalFlags.NONE,
        lambda *_: fail("the screen sharing session was closed by the desktop"))

    widths = physical_widths()
    described, pipelines = [], []
    for index, (node_id, props) in enumerate(streams):
        left, top = props.get("position", (0, 0))
        width, height = props.get("size", (0, 0))
        described.append({"index": index, "left": left, "top": top, "width": width,
                          "height": height, "width_mm": widths.get((left, top))})

        fd = portal.open_pipewire_remote(session)  # one connection per stream
        pipeline = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true ! "
            # Plain system-memory caps: keep DMABuf out, it cannot be read back
            # reliably on every GPU driver.
            "video/x-raw ! "
            # drop-only: thin the stream to at most --fps, never invent frames
            # while the screen is static.
            f"videorate drop-only=true ! video/x-raw,framerate={args.fps}/1 ! "
            "videoconvert ! video/x-raw,format=RGB ! "
            "appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true")

        def on_sample(sink, index=index):
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK
            structure = sample.get_caps().get_structure(0)
            width = structure.get_value("width")
            height = structure.get_value("height")
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                return Gst.FlowReturn.OK
            try:
                data = mapped.data
                # Rows may be padded; the stride follows from the buffer size.
                send({"type": "frame", "index": index, "width": width,
                      "height": height, "stride": len(data) // height}, data)
            except OSError:  # the recorder went away
                GLib.idle_add(loop.quit)
                return Gst.FlowReturn.EOS
            finally:
                buffer.unmap(mapped)
            return Gst.FlowReturn.OK

        pipeline.get_by_name("sink").connect("new-sample", on_sample)
        gst_bus = pipeline.get_bus()
        gst_bus.add_signal_watch()
        gst_bus.connect("message::error", lambda _bus, message: fail(
            f"GStreamer: {message.parse_error()[0].message}"))
        gst_bus.connect("message::eos", lambda *_: fail("the screen stream ended"))
        pipelines.append(pipeline)

    send({"type": "streams", "streams": described})
    for pipeline in pipelines:
        pipeline.set_state(Gst.State.PLAYING)

    def on_stdin(_fd, _condition):
        if not os.read(0, 4096):  # EOF: the recorder exited
            loop.quit()
            return False
        return True

    GLib.io_add_watch(0, GLib.IO_IN | GLib.IO_HUP, on_stdin)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        for pipeline in pipelines:
            pipeline.set_state(Gst.State.NULL)
    return exit_code[0]


if __name__ == "__main__":
    sys.exit(main())
