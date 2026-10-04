# juno capture

Activity monitoring for Juno: use the computer, get events on disk in the
session format Tada's `napsack` recorder writes. See
[`../plan-activity-monitoring.md`](../plan-activity-monitoring.md).

## Status

| Piece | State |
|---|---|
| Recorder, handlers, CLI, checker | done |
| `wayland` backend: portal screen capture | done, verified on GNOME 50 |
| `wayland` backend: evdev input | done, needs the `input` group |
| `wayland` backend: pointer position | not available on Wayland; logged as null |
| `macos` backend: mss frames, pynput input and pointer | done, verified on macOS 15.6 |
| `x11` backend: same sources as `macos` | written, not yet run on X11 |
| `fake` backend (generated frames, scripted input) | done |

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
Accessibility and Input Monitoring to the app you start the recorder from
(the terminal), then quit and reopen it. The recorder refuses to start until
all three are granted, because without Screen Recording frames show only the
wallpaper and `capture.check` cannot tell. Frames do not include the pointer
on either platform; `annotate.request.draw_cursor` draws it.

## Run

```
uv run python -m juno               # run the app until Ctrl+C
uv run python -m capture.check      # check the newest session
uv run pytest
```

`python -m juno` is one loop that starts two subprocesses, the API server
(`python -m server`, FastAPI on uvicorn) and the recorder (`python -m capture`),
and stops both when either exits or on Ctrl+C.

The server's routes so far are `GET /api/status`, `GET /chats` and `/chats/{id}`. A chat is
the message list the `juno` REPL sends to vLLM (`{"role", "content"}` items).
`PUT /chats/{id}` with `{"messages": [...]}` appends them, sends the whole
conversation to the model server in `llm.base_url` (vLLM, as started by `juno`)
and appends its reply; the chat is created if needed. `GET /chats/{id}` returns
it. Each chat is one JSON file, `<data_dir>/chats/<id>.json`, next to the
recorder's `<data_dir>/screen/`.

`GET /chats` lists the stored chats as `{id, title, message_count, updated_at}`
(title: first line of the first user message; `updated_at`: Unix seconds),
in OpenAI's list shape: `?limit=` (1-100, default 20), `?order=desc|asc` (by
last change, default `desc`) and `?after=<id>` for the next page; the response
is `{"object": "list", "data", "first_id", "last_id", "has_more"}`.

`POST /predictions` (optionally `?device=<device_id>`) returns
`{actions, retrieved, timestamp}`: the model's prediction of the user's next
`predict.future_len` actions as an `<actions>` block of `<action>` tags, made
the way Tada's prompted user model makes it, from the last `predict.past_len`
annotated actions, the screenshots of the newest `predict.num_imgs_per_sample`
and earlier activity retrieved with BM25 (`retrieved`). It answers 409 until
that many actions are annotated. The screenshots need a model server that
accepts images.

There are no command-line flags. All three read [`config.yaml`](config.yaml)
from the working directory, or the file named by `JUNO_CONFIG_PATH`. For a
synthetic session that needs no access, set `backend: fake` and `duration: 10`
under `capture`.

Before logging out and in, the group can be used with
`sg input -c 'uv run python -m juno'`.

Sessions go to `~/.local/share/juno/screen/sessions/session_<timestamp>/`
(`capture.data_dir` to change). Defaults match Tada: 5 fps, 120 s frame buffer,
screenshots scaled to 100 dpi, mouse moves not recorded. On a 4K screen those
defaults are heavy; `scale: 0.5` with `buffer_seconds: 10` is much lighter.

With `annotate.live` set, the recorder also uploads each aggregation and its
screenshot to the server in `server.host` (`PUT /aggregations/{id}`), which can
be another machine. The server keeps every device's sessions under
`<data_dir>/screen/devices/<device_id>/sessions/` and annotates each chunk as it
fills; `/sessions`, `/aggregations` and `/annotations` read from there and take
`?device=<device_id>`. A session's id on the server is
`<device_id>-session_<timestamp>`. On a machine that only records, run
`uv run python -m capture` instead of `python -m juno`.

The server annotates on one thread with up to `annotate.max_concurrency` model
calls in flight, taking chunks a recorder just filled first, then ones asked
for with `PUT /annotations/{id}`, then (with `annotate.backfill`) every other
chunk in its store without a finished annotation, most recent first. A call the
provider was too busy for is made again, up to `annotate.max_attempts` times.
`GET /api/queue` shows what is waiting and in flight.

`uv run python -m capture.upload` copies every session already recorded on this
computer to the server, without asking for annotations; run it again to
continue a copy that stopped. Set `capture.device_id` first: the id is part of
every session id the server stores.

## Adding a backend

Implement `ScreenSource`, `InputSource` and `CursorSource` from
`sources/base.py`, return them from a module-level `create(**options)`, and
register the module in `sources/__init__.py`. The conventions the rest of the
pipeline relies on (pynput key and button names, epoch timestamps, global pixel
coordinates) are at the top of `base.py`.
