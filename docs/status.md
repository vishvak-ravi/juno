# Status

## 2026-10-21: Jev keypoints

| Piece | State |
|---|---|
| Per-row Jev request, answers stored per chunk, scan worker under the annotate switch | done; tested with a fake Jev, the SDK request checked against a mock transport; not yet run against TypeSafe |
| `indices` and `moments` on `GET /events` and `GET /sessions/{id}`; `GET /indices` | done; tested with a fake Jev |
| Image token cost per row | not yet measured |

## 2026-10-21: Desktop window, timeline details

| Piece | State |
|---|---|
| Event stepping: a bar pinned above the event with fixed labels and a count, disabled at the ends, arrow keys | done; checked with example data only |
| Day picked from the date input's calendar | done; the calendar opens through `showPicker()`, checked in Electron only |
| Screenshot shown whole, shimmer in its box after 300 ms | done; checked with example data only |

## 2026-10-20: Desktop window layout

| Piece | State |
|---|---|
| Chats, timeline and predictions in the left pane, picked from the bar below it; latest chat open on startup | done; drawn in Electron with the page's example data, not yet against a live server |
| Timeline by day and session, event detail with screenshot and inputs | done; checked with example data only |
| Predictions by run, prediction detail with scores and action lists | done; checked with example data only |
| Devices panel (`GET /control`, switches) and the light on its button | done; checked with example data only |
| Settings panel: server address saved in `settings.json` | done; save, refusal of a bad address and reload checked through the real preload |
| Camera minimized to a chip | done; frames keep reaching the recognizer while minimized (checked with a generated stream, not a camera) |

## 2026-10-13: Tablet drawing page

| Piece | State |
|---|---|
| `/draw` page: canvas, colours, draw and erase, pencil pressure | written, run only in tests; not yet opened on an iPad |
| Server-side recorder for it (`server.draw`, `capture.sources.pushed`) | done, tested end to end with a socket client |
| Canvas size after the tablet rotates | fixed at load; rotating needs a reload |

## 2026-10-12: Capture backends

| Piece | State |
|---|---|
| Recorder, handlers, CLI, checker | done |
| `wayland` backend: portal screen capture | done, verified on GNOME 50 |
| `wayland` backend: evdev input | done, needs the `input` group |
| `wayland` backend: pointer position | not available on Wayland; logged as null |
| `macos` backend: mss frames, pynput input and pointer | done, verified on macOS 15.6 |
| `x11` backend: same sources as `macos` | written, not yet run on X11 |
| `fake` backend (generated frames, scripted input) | done |
| Recording and annotation switches (`/control`, Status page) | done; restarting a session tested only on the `fake` backend |

The model server (vLLM) is started by `juno`, a symlink to `~/gemma_chat.py`,
which is not in this repository.
