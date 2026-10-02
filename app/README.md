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
| `fake` backend (generated frames, scripted input) | done |
| `x11` backend | stub |

The recorder reuses napsack's queues, burst aggregation and save code by
import, and replaces only its two handlers (the parts bound to `pynput` and
`mss`). Output is therefore napsack's.

## Setup (once)

```
uv sync --extra wayland
sudo usermod -aG input $USER     # then log out and back in
```

Screen capture needs the system Python to have PyGObject and GStreamer's
PipeWire plugin, which Ubuntu's GNOME desktop ships. The first run shows the
desktop's share dialog once; the grant is remembered in
`~/.local/state/juno/portal-token`.

## Run

```
uv run python -m capture            # record until Ctrl+C
uv run python -m capture.check      # check the newest session
uv run python -m capture --backend fake --duration 10   # synthetic session, no access needed
uv run pytest
```

Before logging out and in, the group can be used with
`sg input -c 'uv run python -m capture'`.

Sessions go to `~/.local/share/juno/screen/sessions/session_<timestamp>/`
(`--data-dir` to change). Defaults match Tada: 5 fps, 120 s frame buffer,
screenshots scaled to 100 dpi, mouse moves not recorded. On a 4K screen those
defaults are heavy; `--scale 0.5 --buffer-seconds 10` is much lighter.

## Adding a backend

Implement `ScreenSource`, `InputSource` and `CursorSource` from
`sources/base.py`, return them from a module-level `create(**options)`, and
register the module in `sources/__init__.py`. The conventions the rest of the
pipeline relies on (pynput key and button names, epoch timestamps, global pixel
coordinates) are at the top of `base.py`.
