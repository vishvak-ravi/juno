# Desktop client

The Electron client (`apps/desktop/`): one window, `src/renderer/index.html`, the
browser of the API server in `packages/server/`. It draws with the modules in
`apps/web/src/` (the server's routes in `api.js`, the shared views and the
markdown reader), which the phone page the server serves (`apps/web/index.html`)
draws with too; each page keeps its own shell and chat flow.

## Window

The left pane shows one of three lists, picked by the buttons in the bar
pinned below it; the detail pane on the right shows what is open in it. The
divider between them drags to set the left pane's width, remembered on the
computer (`juno.aside`). What takes long enough to notice to arrive shows as
shimmering lines in its place. On
startup the chats list is shown with the most recently changed chat open. The
timeline pane is made once and kept: shown again, it shows what it had, then
asks what changed.

| Button | Left pane | Detail pane |
|---|---|---|
| Chats | `GET /chats`, newest first | the chat (`GET /chats/{id}`) and its composer |
| Timeline | sessions, newest first, from the start of this week, with a heading where the day changes and "Earlier" adding a week; a session opens to its **sequences** (the rows captioned together), newest first, each to its actions with a divider where a moment starts; the open action's session and sequence summaries stay pinned under the day heading as the list scrolls (the sequence's once it outruns the list), pressed to fold them; the **moment rail** down the pane's left edge, every moment of the sessions listed in the list's order with a divider per day, the wheel over it zooming, the moment under the pointer staying put; **Now** scrolls to the top and the calendar to today | the **calendar**: a week (or a day, or a month) of sessions as blocks on their days, each with one index curving down its right side and its moments pinned on its left, the moment types shown chosen by the chips above it, now marked in red; a block opens its session on the left, a pin opens its action. An **action** opens in the calendar's place: the ribbon of its day across the top (its sessions and moments, the action marked; pressed, or Escape, to go back), the action's caption, its parent sequence's caption, a button per Claude Code conversation its moments carry, the screenshot with the inputs replayed over it, the frames of its sequence and the sequences around it (the arrow keys step through them), and its inputs folded |
| Predictions | the graded runs; a run opens to its predictions | one graded prediction (`GET /predictions/{id}`) |
| Devices | opens a panel above the bar: this computer's recorder, the annotation switch and each device's recording switch (`GET /control`, polled every `poll_seconds`) | |
| Settings | this app's settings, this computer's recorder's and the server's, each by section with an Environment entry, read together (`window.juno.describePrefs`, `describeDeviceSettings`, `GET /settings`); a source that cannot be read says why in its place | the open section's form (see Settings below) |

The light on the devices button is green while any device records, amber
while this computer's recorder has exited or a device has not yet done what
its switch asks, grey when nothing records, hollow when the server cannot be
reached.

The timeline and predictions views are drawn from the routes as they are; what
the client computes from the rows (`src/renderer/rows.mjs`):

| Shown | From |
|---|---|
| A day's tint in the month | `GET /events/activity?edges=` with the local midnights of the six weeks shown; the square root of the day's count over the busiest day in view |
| The sessions list | `GET /changes?since=` from the local start of this week, every session in range and the cursor to go on from; "Earlier" fetches the week before the earliest loaded, `GET /sessions?since=&until=&order=desc&limit=100`, every page |
| What changed | every `live_seconds` of `GET /control` while the pane is shown, `GET /changes?cursor=&since=&events=`, `events` the ids of the sessions whose events the pane holds: each session listed is put in the list anew, its changed events merged by id into the ones held (`removed` taken out; `whole` replacing them), its open sequences drawn again, the open action's captions set again, and the calendar's blocks and pins drawn again with `GET /indices` read again when a changed session is on its days. What was drawn stays where it is: a session or action landing above slides the rest down, unless motion is reduced; scrolled down, the list keeps its place and the Now button counts the sessions that landed above the view |
| A session's sequences | `GET /sessions/{id}`, the first time the session is opened (or a block, a pin or a rail line opens it); its events grouped by their `sequence`, each sequence's caption its events' `sequence_caption` |
| A moment's divider | above the action it starts at, under its sequence; its text from the moment's `type` and the assistant in its `label`, its diamond in the type's colour; hidden with its type |
| The moment rail | one line per moment of the sessions listed, from the sessions' `moments`, in the list's order, coloured by `type`, with a divider where the day changes; a line's reading (its text, day and time) shows beside it on hover. Lines are `juno.timeline.pitch` px apart, 2 to 14, remembered on the computer; the wheel over the rail changes it, the line under the pointer keeping its place (the rail alone shifts for that; the list stays put); when the lines outrun the rail it scrolls with the list. The line nearest the pointer, within half a pitch, is the one read and pressed. The line of the moment the list is at is ringed: the open action's moment (the session's latest moment starting at or before it) while its row is on screen, else the first action's on screen. Pressing a line opens its session, scrolls the list to its divider, lit for 1.5s, and opens its action |
| The calendar's days | the week holding the day shown, from the locale's first weekday; one day; or the six weeks of the month, pressed to open that week |
| A session's blocks | `GET /sessions?since=&until=` over the days shown, every page: one block per day the session touches, from and to as fractions of that day; "now" as its end while `GET /control` reports a device recording it |
| An index's curve | `GET /indices?since=&until=` over the days shown, the index picked above the calendar (`agent` when there is one, else the first in `names`); the bucket means averaged over three buckets, broken where a bucket is missing or has no value, drawn as an area from the block's left edge |
| A moment's pin | a session's `moments` on their day; moments closer than a pin's height share one pin (ringed), in the colour most of them have, pressed to open the first; the chips above the calendar hide a type everywhere |
| The ribbon's pins | the same, a hundredth of the day apart, pressed to open the moment's action; the rest of the ribbon goes back to the calendar |
| The ribbon | the open action's day: the blocks and pins of the sessions listed that touch it, now, and a mark at the action |
| A replay | the action's inputs folded into items (`src/renderer/activity.mjs`), one stop per item at its offset from the screenshot; a click or scroll is drawn where it happened, in the screenshot's own pixels, keys as a bubble; play steps through them |
| The frames | the events of the action's sequence and of one sequence either side, a divider between sequences, the open one marked; a frame opens its action, the arrow keys step through the session's events |
| A conversation's button | each distinct `conversation` among the event's `moments`, drawn when the event's `device_id` is this computer's (`resolve_device_id`, asked of the checkout's Python at startup); on another device's event, a line naming that device. Pressing it runs `claude --resume <session_id>` in a new Ghostty window in `cwd` (`src/main/terminal.js`), without the variables of a Claude Code the app was started from |
| An input's time | its `timestamp` minus its event's `timestamp` |
| A run | `GET /predictions?limit=100` rows grouped by `run`; its time read from the run's name (`YYYYMMDD_HHMMSS`); mean reward over the rows with a reward; failed rows are those with an `error` |
| Model names | `predictor.model` and `grader.model` without the provider before the first `/` |
| Action lists | the `<action>` elements of `prediction` and `ground_truth`; green where both hold the same text at the same position |

A screenshot is requested only when its event is opened, and then the screenshots
of the actions either side of it, as many as the app's prefetch setting, are fetched ahead, so stepping to them shows
each at once; a prediction's
prompts and the predictor's screenshots (`GET /predictions/{id}`) only when the
prediction is opened, the screenshots only when their section is expanded.
With `JUNO_TIMING_FILE` set, the main process (`src/main/server.js`) appends one JSON line per GET to that file: the route, milliseconds to the headers and to the parsed body, the server's `Server-Timing` milliseconds and the bytes.
Setup and run commands are in the [README](../README.md).

## Recorder

The client starts the recorder, `python -m capture` run with the checkout's `.venv/bin/python`
from the checkout root (so it reads the same `.env`), with `JUNO_SERVER_URL` set to the
server address in the app's settings, when it starts, and
quits only once the recorder has saved its session. The devices panel shows
whether it is running and, if it exited, its status and last log lines; it is not
restarted until the app is. Status 2 is a backend that could not start: on macOS
a permission not granted, see [`capture.md`](capture.md). macOS grants
permissions to the app the client was launched from, the terminal when it is
started with `npm start`.

## Linux sandbox (Ubuntu 24.04 and later)

Electron aborts at startup with
`The SUID sandbox helper binary was found, but is not configured correctly`.
Ubuntu's AppArmor blocks unprivileged user namespaces
(`kernel.apparmor_restrict_unprivileged_userns = 1`) for any binary without a
profile allowing `userns`, so Chromium falls back to the setuid helper
`node_modules/electron/dist/chrome-sandbox`, which npm installs without root
ownership or the setuid bit. Give the npm Electron binary a profile, the same
one `/etc/apparmor.d/code` gives VS Code. Run from `apps/desktop/`:

```
sudo tee /etc/apparmor.d/juno-electron >/dev/null <<EOF
abi <abi/5.0>,
include <tunables/global>

profile juno-electron $PWD/node_modules/electron/dist/electron flags=(unconfined) {
  userns,
  include if exists <local/juno-electron>
}
EOF
sudo apparmor_parser -r /etc/apparmor.d/juno-electron
```

The profile names the binary's path, so it survives `npm ci` and Electron
updates; redo it only if the checkout moves. Making `chrome-sandbox` root-owned
with mode 4755 also works but is undone by every reinstall.

## Settings

Each source describes its settings the same way (`juno.settings`: sections of
fields, each with its kind, value, default and description), and one form
(`apps/web/src/settings.js`) draws a section: a field per setting, the
advanced ones folded, "Use default" where a value differs from its default,
and Save sending only what changed as `{set, reset}` by dotted path. A refused
change says why under each field and saves nothing. A field marked local
(`juno.config.LOCAL`: the bash and Claude tools' switches, sandbox, command,
permission mode and directory) is shown as it is, with the command that changes
it on the server's machine. The pane is made once and kept, like the timeline's;
it reads a source again when a change to it is saved.

| Source | Kept in | A save |
|---|---|---|
| This app (`app.*`: server address, prefetch, passive agent module) | `settings.json` in Electron's userData directory (`src/main/prefs.js`, `src/main/settings.js`; `~/.config/juno-client` on Linux, `~/Library/Application Support/juno-client` on macOS) | a new server address reloads the window and restarts the recorder; the passive agent module changes when the app restarts, which the view offers |
| This computer (`capture.*`, `claude_hook.*`) | `<data_dir>/device.json`, through the checkout's Python (`src/main/device.js`, `python -m juno.settings device`) | restarts the recorder, which saves its session first |
| Server | `<data_dir>/settings.json` on the server (`GET`/`PATCH /settings`) | applies when the server restarts; the list counts the saved changes waiting for it |

Each source's Environment entry lists what its process reads from `.env`, an
API key only as set or not, and which settings need a key that is not set.
With no server address saved, it is `JUNO_SERVER_URL`, else
`http://<JUNO_SERVER_HOST>:<JUNO_SERVER_PORT>` from `.env` at the checkout root
(defaults `127.0.0.1` and `8765`).

## Passive agent module

The app's passive agent setting turns on the passive agent module: the camera
preview with hand keypoints and the gesture roles (`src/renderer/hand.js`), and
the dictation helper (`native/juno-dictate`, built by `npm run native`, macOS
only) that the dictate role feeds the message box from. The gesture model is
`models/gesture_recognizer.task`, downloaded by `npm run model`. The main
process starts the helper only when the setting was on at startup and tells the pages
through `window.juno.passiveAgent`; off, the pages show the composer
only and start neither the camera nor the helper.
