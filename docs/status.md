# Status

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
