# Capture and the app server

Activity monitoring for Juno (`app/`): use the computer, get events on disk in
the session format Tada's `napsack` recorder writes. Per-backend state is in
[`status.md`](status.md).

The recorder reuses napsack's queues, burst aggregation and save code by
import, and replaces only its two handlers (the parts bound to `pynput` and
`mss`). Output is therefore napsack's.

## Setup (once)

```
uv sync
```

`backend: auto` picks `macos` on macOS, else `wayland` or `x11` from the
session environment.

**Wayland:** `sudo usermod -aG input $USER`, then log out and back in.
`evdev` is built from source at install time, so `uv sync` needs a C compiler
and the kernel headers. Screen capture needs the system Python to have PyGObject and GStreamer's
PipeWire plugin, which Ubuntu's GNOME desktop ships. The first run shows the
desktop's share dialog once; the grant is remembered in
`~/.local/state/juno/portal-token`.

**macOS:** in System Settings > Privacy & Security, grant Screen Recording,
Accessibility and Input Monitoring to the app the recorder is started from:
the terminal the desktop client, or `python -m capture`, was started from.
Then quit and reopen it. The first start adds the app to the two lists that
have a request dialog. The recorder refuses to start until
all three are granted, because without Screen Recording frames show only the
wallpaper and `capture.check` cannot tell. Frames do not include the pointer
on either platform; `annotate.request.draw_cursor` draws it.

## Run

Commands are in the [README](../README.md). The API server (`python -m server`,
FastAPI on uvicorn) runs on one machine. The recorder (`python -m capture`) is
started on each computer by the desktop client ([`client.md`](client.md)), or by
hand with `uv run python -m capture`. The server's routes are listed
at `http://<host>:8765/docs`. Chats are completed by the model server in
`llm.base_url` (vLLM); each chat is one JSON file, `<data_dir>/chats/<id>.json`,
next to the recorder's `<data_dir>/screen/`.

There are no command-line flags. Both read [`config.yaml`](config.yaml)
from the working directory, or the file named by `JUNO_CONFIG_PATH`. What
differs between devices, the server host and the device id, is read from `.env`
instead (see [`.env.example`](.env.example)). For a
synthetic session that needs no access, set `backend: fake` and `duration: 10`
under `capture`.

Before logging out and in, the group can be used with
`sg input -c 'npm start'` (or `sg input -c 'uv run python -m capture'`).

Sessions go to `~/.local/share/juno/screen/sessions/session_<timestamp>/`
(`capture.data_dir` to change). Defaults match Tada: 5 fps, 120 s frame buffer,
screenshots scaled to 100 dpi, mouse moves not recorded. On a 4K screen those
defaults are heavy; `scale: 0.5` with `buffer_seconds: 10` is much lighter.

With `annotate.live` set, the recorder also uploads each aggregation and its
screenshot to the server at `JUNO_SERVER_HOST` (`PUT /events/{id}`), which can
be another machine. The server keeps every device's sessions under
`<data_dir>/screen/devices/<device_id>/sessions/` and annotates each chunk as it
fills. A session's id on the server is `<device_id>-session_<timestamp>`.

The recorder also reports to the server every `control.poll_seconds`
(`PUT /control/devices/<device_id>/report`) and records only while that
device's switch is on; its first report adds the device. Turning the switch off
ends the session, and turning it on starts a new one. If the server can't be
reached, the recorder keeps doing what it was doing. The server's own switch
pauses the annotation queue: queued chunks stay pending until it is on again.
Both switches are on the clients' Status page (`GET /control`, `PATCH
/control/server`, `PATCH /control/devices/<device_id>`) and are stored in
`<data_dir>/control.json` on the server.

A tablet records by opening `http://<server>:8765/draw`: a black canvas to
draw on, sent with each pointer event over `/draw/socket`. The server runs the
recorder for it (`server.draw`) on the `capture.sources.pushed` backend, writes
the session under `<data_dir>/screen/draw/` and uploads it to itself through
`PUT /events` as the device `draw.device_id`, so it is stored, annotated and
switched on and off like a computer's. Each page load is a blank canvas and a
new session.

Edits and deletes of events are kept in the session's `event_edits.json`; the
row and its screenshot stay on disk with every field the recorder wrote.

The server annotates on one thread with up to `annotate.max_concurrency` model
calls in flight, taking chunks a recorder just filled first, then (with `annotate.backfill`) every other
chunk in its store without a finished annotation, most recent first. A call the
provider was too busy for is made again, up to `annotate.max_attempts` times.
`GET /api/queue` shows what is waiting and in flight; `GET /api/integrity`
shows, per device and session, what arrived and what is annotated.

`uv run python -m capture.upload` copies every session already recorded on this
computer to the server, without asking for annotations; run it again to
continue a copy that stopped. Set `JUNO_DEVICE_ID` first: the id is part of
every session id the server stores.

## Adding a backend

Implement `ScreenSource`, `InputSource` and `CursorSource` from
`sources/base.py`, return them from a module-level `create(**options)`, and
register the module in `sources/__init__.py`. The conventions the rest of the
pipeline relies on (pynput key and button names, epoch timestamps, global pixel
coordinates) are at the top of `base.py`.
