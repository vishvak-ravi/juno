# Status

## 2026-10-23: Proposals in the clients

| Piece | State |
|---|---|
| `lattice/proposals.py`: a run over one day (`propose_run`, the body `scripts/propose_actions.py` ran), every insight ranked per task with its score, the proposer's cut candidates (`considered` in the reply format), calls recorded with prompt, reply, seconds and tokens; runs under `<data dir>/proposals/<run>/` with `decisions.jsonl` as the trail; `state_of` | done; 7 tests with a fake model |
| `/proposals` routes: the feed by state (a first listing writes `listed`), one proposal in full, `PATCH` events, runs listed and started (202; 409 while one goes), one task proposed for on request, the rate | done; 6 tests through `create_app(dawn_call=)` |
| `proposals.propose_at`: the server proposes for the day before every morning (`server.propose`) | done; the wait is tested, the loop is not |
| The desktop Proposals pane (feed, ledger, proposal, task pages) and the phone's `#/proposals` screens, over `apps/web/src/proposals.js` (6 tests) and `proposalPage` in `views.js` | done; drawn only with the window's example data, not yet against a server |
| The predictions pane and the phone's predictions screens | removed; `GET /predictions` and `POST /predictions` stay for scripts |
| Dispatching an accepted proposal to the agent | not built; accepted is a recorded intent |

## 2026-10-23: Conversations imported; the lattice over activity and messages

| Piece | State |
|---|---|
| `juno.conversations`, `server.conversations.FileConversations` (one file per thread under the device), `capture.chats.claude_code` (the transcript reader), `scripts/import_conversations.py` | done; reader and store tested; not yet run on this machine's 115 transcripts |
| `lattice.build` reads the types in `lattice.events`; a conversation is one session of `<speaker>: <text>` turns cut at `turn_chars`; `SourceInteraction.conversation` points a node at its turn and the node route shows the turn's text | done; tested with the fake model and through the routes |
| Build over the store with conversations imported | not yet run; plan: `scripts/bench_routes.py snapshot`, a config copy pointing at it, import, `scripts/build_lattice.py` |
| `GET /events` listing messages; moments from imported turns deduplicated against the hook's lines by prompt id | not started |
| The hook sending the prompt's text, or a sync of the transcripts, so an installed device stays current | not started |
| The desktop client's lattice view on a node whose source is a turn (`caption` is the turn's text, `event_ids` empty) | checked against the server's build of 2026-10-23: the turn's text is listed under From, clamped to three lines, with no Timeline button |

## 2026-10-23: Desktop-app prompts as moments

| Piece | State |
|---|---|
| `claude-desktop` prompts kept with their `entrypoint` and the app's own session id (`X-Claude-Code-Host-Session`); both on `conversation` in `GET /events` and `GET /sessions/{id}` | done; tested. A hook installed without the third header must be installed again |
| The conversation chip (Claude's mark, assistant, where it ran); the desktop app's opened by `claude://code/continue?session=<host_session_id>` | done; unit tested; drawn in the client. A link made from `local_` plus Claude Code's id opened the app on a new chat in 14 of 108 sessions, whose app id differs |

## 2026-10-23: Behavior lattice build and Dawn's action proposals

| Piece | State |
|---|---|
| `lattice` package: the library's prompts verbatim, observe, layers, build; `scripts/build_lattice.py` | done; 7 tests with a fake model |
| Builds over the store (2,140 captions, 7,646 interactions, 8 sessions at the 3-hour gap, 310 observer windows) against the local vLLM Gemma (16k context) | done: `lattice_20261023_025734` in 3.5 min, 1,706 observations, 16 / 10 / 7 insights by layer, 344 calls; the two biggest days (10-03, 10-21) overflow the context at layer 1 and contribute no insights. vLLM refuses a prompt longer than its context less `lattice.max_tokens`; at 8192 a first build lost 80 of 310 windows and 3 of 7 days |
| The same build with vLLM at a 32k context (`--max-model-len 32768` in `~/.config/systemd/user/vllm.service`; 11.65 GiB of KV cache, 9 full-length requests at once) | done: `lattice_20261023_030545` in 4.3 min, 1,732 observations, 21 / 16 / 7 insights by layer, 352 calls, none failed; every day has layer 1 insights |
| Build with `lattice.layers` number:40, day, all, the first grouping about the size of the authors' example lattice's | done: `lattice_20261023_034949` in 5.1 min, 1,694 observations, 121 / 37 / 7 insights by layer, 538 / 119 / 34 edges; 3 of 43 first-layer groups lost to unreadable JSON from the format call. The five earlier builds are moved to `data/lattice/` at the checkout root |
| `lattice/dawn.py`, `lattice/rerank.py`, `scripts/propose_actions.py`: tasks of a day, utility scores, reranked insights, two actions per task | done; 7 tests with a fake model; run on 2026-10-22 (1,018 captions, 7 windows): 3 tasks, 2 kept at 0.75, 2 actions each, 21 calls in 54 s, `llm` reranker |
| Voyage reranker (`dawn.rerank_model: voyage/rerank-2.5`) | needs `VOYAGE_API_KEY` in `.env`; `llm` works without it |
| Annotation backfill: 1,308 chunks without a finished caption | stalled: `annotate.api_base` points at a llama.cpp server that is not running, and the GPU holds vLLM |

## 2026-10-23: Behavior lattice routes

| Piece | State |
|---|---|
| `GET /lattice`: every build newest first as `BuildSummary` (`LatticeBuild` with the count of its sessions in place of them) (`server.routes.lattice`) | done; tested against a lattice stored with `FileLattices` |
| `POST /lattice`: body `device`, `since`, `until` and the `LatticeParams` knobs, each defaulting from `config.lattice`; calls `lattice.build.new_build`, saves the record, runs `build_lattice` as an `asyncio` task kept in `app.state.lattice_tasks`; 202 with the summary, 409 while a build runs; a build that raises without recording it is marked `failed` | done; tested with an injected `build_lattice` (`create_app(build_lattice=)`) |
| `GET /lattice/{build_id}`: the `LatticeBuild` with its sessions | done; tested |
| `GET /lattice/{build_id}/lattice`: the whole `Lattice`; 404 until the build is done | done; tested |
| `GET /lattice/{build_id}/layers/{layer}`: `{layer, spec, nodes, edges}`; layer 0 is the observations with no spec and no edges | done; tested |
| `GET /lattice/{build_id}/nodes/{layer}/{node_id}`: the node with `parents`, `children`, `support` (every node under it by layer) and `sources` (the build's sessions its observations came from, each interaction with its action's `caption` and the `event_ids` the action covers through `framed_rows`) | done; tested with rows uploaded through `PUT /events` and a caption spanning frames 1-2 |
| `app.state.lattices` (`FileLattices` under `<data dir>/lattice`), `app.state.lattice` (`config.lattice`) | done |
| The desktop client's Lattice view (`apps/desktop/src/renderer/lattice.mjs`): the build picked in the pane header, one layer at a time picked by the switch under it, a node's page on the right moving the switch as its parents and children are followed, its source actions opening in the timeline | done; checked against a stored example lattice served by the routes, not yet against a real build |
| The phone page | no lattice view |

## 2026-10-22: Live timeline

| Piece | State |
|---|---|
| `GET /changes?cursor=&since=&until=&events=`: the sessions changed since the cursor, with the changed events of the ones named (`server.routes.changes`) | done; tested |
| A session's fingerprint (`version` of `Events`, `ActivityCaptions`, `QuestionAnswers`; the device's prompts in its span) numbers its changes, and its events are built once per fingerprint with the rows that differ from the build before (`server.versions`) | done; tested through `/changes` |
| `control.live_seconds` (2), returned by `GET /control`: how often the client asks | done |
| The desktop timeline pane is kept across views and asks every `live_seconds` while shown; sessions, sequences, actions and dividers are reconciled by key (`reconcile` in `apps/web/src/views.js`), what lands above sliding the rest down; the Now button counts sessions that landed above a scrolled view | done; not yet run against a recording device |
| The calendar's blocks and pins are drawn again (one `GET /indices`) when a changed session is on its days; the month's counts read again | done |
| The phone page | not live |

Against the rows cache alone (`--label built-cache`, same snapshot, day and passes), in ms:

| Request | Rows cache p50 | Now cold | Now p50 | Now p95 |
|---|---|---|---|---|
| calendar (`/events/activity`) | 18 | 2,290 | 21 | 22 |
| day events (`/events?…&limit=100`) | 63 | 87 | 27 | 44 |
| day indices (`/indices`) | 46 | 18 | 31 | 504 |
| day events, next page (`after=`) | 62 | 14 | 41 | 73 |
| day sessions (`/sessions`) | 44 | 20 | 29 | 40 |
| open session (`/sessions/{id}`) | 22 | 13 | 15 | 38 |
| open day (events and indices together) | 80 | 41 | 35 | 89 |

## 2026-10-22: Timeline routes read only what they need

| Piece | State |
|---|---|
| `FileEvents.rows` keeps each session's parsed rows, parsed again when the size or modification time of its `event_rows.jsonl` changes | done; tested (a row uploaded after a read is in every later read) |
| `/events`, `/indices` and `/sessions` build only the sessions with a row between `since` and `until` (`sessions_between`) | done; tested |
| `/events/activity` counts from rows and edits, without building events (`stored_timestamps`) | done; tested |
| The first request after a start parses every rows file (1.7 s on the snapshot) | not warmed at startup |
| The parsed rows stay in memory: about 470 MB at peak for the snapshot's 27,709 rows (80.6 MB of `.jsonl`) | grows with the store |

Against the baseline (`--label rows-cache-range`, same snapshot and day, 1 cold and 5 warm passes), in ms:

| Request | Baseline p50 | Now cold | Now p50 | Now p95 |
|---|---|---|---|---|
| calendar (`/events/activity`) | 3,965 | 1,738 | 18 | 20 |
| day events (`/events?…&limit=100`) | 4,167 | 72 | 63 | 413 |
| day indices (`/indices`) | 3,999 | 64 | 46 | 393 |
| day events, next page (`after=`) | 3,930 | 97 | 62 | 73 |
| day sessions (`/sessions`) | 4,181 | 72 | 44 | 45 |
| open session (`/sessions/{id}`) | 57 | 32 | 22 | 24 |
| open day (events and indices together) | 8,555 | 490 | 80 | 462 |

## 2026-10-22: Timeline route timings

| Piece | State |
|---|---|
| `Server-Timing: app;dur=` on every response and a `server.timing` log line with duration and bytes (`server.timing`) | done; tested |
| `JUNO_TIMING_FILE`: the desktop client appends route, ms to headers and body, server ms and bytes per GET | done; not yet run in the app |
| `scripts/bench_routes.py` snapshot and run | done; run on `~/.local/share/juno-bench/2026-10-22` (45 sessions, 27,709 rows, day 2026-10-22) |

Baseline (`--label baseline`, 1 cold and 5 warm passes, server on this machine, annotation off), in ms:

| Request | Cold | p50 | p95 | Server p50 | Bytes |
|---|---|---|---|---|---|
| calendar (`/events/activity`) | 6,518 | 3,965 | 4,109 | 3,964 | 689 |
| day events (`/events?…&limit=100`) | 4,184 | 4,167 | 5,174 | 4,166 | 136,271 |
| day indices (`/indices`) | 4,046 | 3,999 | 5,016 | 3,998 | 1,609 |
| day events, next page (`after=`) | 3,860 | 3,930 | 6,454 | 3,929 | 66,966 |
| day sessions (`/sessions`) | 4,305 | 4,181 | 4,250 | 4,180 | 636 |
| open session (`/sessions/{id}`) | 69 | 57 | 66 | 55 | 835,502 |
| open day (events and indices together) | 8,704 | 8,555 | 9,087 | 8,553 | 137,880 |

Server time is within 1 ms of the whole request on every route: the bytes take no measurable time on this machine. `/events`, `/events/activity` and `/indices` each build every stored event (`stored_events`), and `/sessions` every session's (`session_of`), before filtering by time, about 4 s; the two requests of a day together take their sum. The one-session route, which builds one session, answers 835 KB in 57 ms.

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
