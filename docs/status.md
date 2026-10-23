# Status

## 2026-10-22: Store behind repositories

| Piece | State |
|---|---|
| `Events`, `ActivityCaptions`, `QuestionAnswers`, `Control` with file implementations; routes, workers, chat tools and scripts go through them | done; tested |
| `scripts/migrate_store.py` | done; tested, and run on copies of two real sessions (7,392 rows, 740 captions: no integrity issues). The live store is not migrated yet |
| Database implementations | not started |
| Claude Code prompts (`server.hooks`, `<device dir>/hooks/`) and chats (`server.chats`) | still read as files by their callers |

## 2026-10-22: Claude Code prompts as moments

| Piece | State |
|---|---|
| `POST /hooks/claude-code`, the origin gate, prompts placed on the device's row | done; tested, and run with an interactive `claude` in tmux (kept) and `claude -p` (left out) against a server on this checkout |
| `python -m capture.claude_hook install` / `uninstall`, offered by the client install | done; tested against a scratch `CLAUDE_CONFIG_DIR` |
| "Resume in Claude Code" on the event: a new Ghostty window running `claude --resume` in the conversation's directory | done on Linux, pressed in the client against rows uploaded by hand (the recorder writes rows only on real input); macOS (`open -na Ghostty`) not yet run |
| Cloud sessions, the Claude desktop app, IDE extensions, claude.ai chats | left out at the route |

## 2026-10-22: MCP over the record

| Piece | State |
|---|---|
| `list_sessions`, `find_events`, `get_event` replace `list_annotations`, `get_annotation`, `predict_next_actions`, over MCP and in Juno's chat | done; tested end to end over MCP with a fake Jev; run read-only against the stored sessions of 2026-10-21 |
| `get_event` returns the screenshot as an MCP image | done over MCP; Juno's chat model is told an image exists but not shown it |
| Each call reads every stored session (about 3 s over 3,000 events) | not optimized |
| Tablet captions: new prompt and chunking | not started |

## 2026-10-21: Activity view

| Piece | State |
|---|---|
| Inputs folded into typed text, shortcuts, key runs, clicks and scrolls, in the client (`activity.mjs`) | done; unit tested, and run over a recorded session of 16k inputs (700 items) |
| Activity view, the timeline's default: newest first, a colour per kind | done; not yet seen drawn, Electron did not start from the agent's shell |
| Folding behind an API route | not yet; its gaps (`GAPS`) move to `config.yaml` then |

## 2026-10-21: Jev keypoints

| Piece | State |
|---|---|
| Per-row Jev request, answers stored per chunk, scan worker under the annotate switch | done; tested with a fake Jev, the SDK request checked against a mock transport; not yet run against TypeSafe |
| `indices` and `moments` on `GET /events` and `GET /sessions/{id}`; `GET /indices` | done; tested with a fake Jev |
| Moment dividers in the linear timeline; Indices view with a lane per index | done; drawn with the page's example data only |
| Moment rail down the linear timeline's left edge: lines by type, heading on hover, press to scroll to the divider | done; checked in a browser with the page's example data |
| Index minimap down the linear timeline's right edge, press to jump; the linear view fetched around a time and paged both ways | done; checked in a browser with the page's example data |
| Image token cost per row | not yet measured |

## 2026-10-21: Desktop window, timeline details

| Piece | State |
|---|---|
| Event stepping: a bar pinned below the event, clear of the camera, with fixed labels and a count, disabled at the ends, arrow keys | done; checked with example data only |
| Day picked from a calendar in the window's theme, each day tinted by its event count (`GET /events/activity`) | done; the route is tested, the calendar checked with example data only |
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
