# Desktop client

The Electron client (`apps/desktop/`): one window, `src/renderer/index.html`, the
browser of the API server in `packages/server/`. It draws with the modules in
`apps/web/src/` (the server's routes in `api.js`, the shared views and the
markdown reader), which the phone page the server serves (`apps/web/index.html`)
draws with too; each page keeps its own shell and chat flow.

## Window

The left pane shows one of three lists, picked by the buttons in the dark bar
pinned below it; the detail pane on the right shows what is open in it. On
startup the chats list is shown with the most recently changed chat open.

| Button | Left pane | Detail pane |
|---|---|---|
| Chats | `GET /chats`, newest first | the chat (`GET /chats/{id}`) and its composer |
| Timeline | sessions, newest first, from the start of this week, with a heading where the day changes and "Earlier" adding a week; a session opens to its **sequences** (the rows captioned together), newest first, each to its actions with a divider where a moment starts; the **moment rail** down the pane's left edge, every moment of the sessions listed in the list's order with a divider per day, its zoom (the buttons above it, or Ctrl and the wheel) setting how many pixels a moment takes; **Now** scrolls to the top and the calendar to today | the **calendar**: a week (or a day, or a month) of sessions as blocks on their days, each with one index curving down its right side and its moments pinned on its left, the moment types shown chosen by the chips above it, now marked in red; a block opens its session on the left, a pin opens its action. An **action** opens in the calendar's place: the ribbon of its day across the top (its sessions and moments, the action marked; pressed, or Escape, to go back), the action's caption, its parent sequence's caption, a button per Claude Code conversation its moments carry, the screenshot with the inputs replayed over it, the frames of its sequence and the sequences around it (the arrow keys step through them), and its inputs folded |
| Predictions | the graded runs; a run opens to its predictions | one graded prediction (`GET /predictions/{id}`) |
| Devices | opens a panel above the bar: this computer's recorder, the annotation switch and each device's recording switch (`GET /control`, polled every `poll_seconds`) | |
| Settings | opens a panel above the bar: the server address (see below) | |

The light on the devices button is green while any device records, amber
while this computer's recorder has exited or a device has not yet done what
its switch asks, grey when nothing records, hollow when the server cannot be
reached.

The timeline and predictions views are drawn from the routes as they are; what
the client computes from the rows (`src/renderer/rows.mjs`):

| Shown | From |
|---|---|
| A day's tint in the month | `GET /events/activity?edges=` with the local midnights of the six weeks shown; the square root of the day's count over the busiest day in view |
| The sessions list | `GET /sessions?since=&until=&order=desc&limit=100`, every page, from the local start of this week with no `until`; "Earlier" fetches the week before the earliest loaded |
| A session's sequences | `GET /sessions/{id}`, the first time the session is opened (or a block, a pin or a rail line opens it); its events grouped by their `sequence`, each sequence's caption its events' `sequence_caption` |
| A moment's divider | above the action it starts at, under its sequence; its text from the moment's `type` and the assistant in its `label`, its diamond in the type's colour; hidden with its type |
| The moment rail | one line per moment of the sessions listed, from the sessions' `moments`, in the list's order, coloured by `type`, with a divider where the day changes; a line's reading (its text, day and time) shows beside it on hover. Lines are `juno.timeline.pitch` px apart, 2 to 14, remembered on the computer; when they outrun the rail it scrolls with the list. A shaded band spans the lines of the sessions in view. Pressing a line opens its session, scrolls the list to its divider, lit for 1.5s, and opens its action |
| The calendar's days | the week holding the day shown, from the locale's first weekday; one day; or the six weeks of the month, pressed to open that week |
| A session's blocks | `GET /sessions?since=&until=` over the days shown, every page: one block per day the session touches, from and to as fractions of that day; "now" as its end while `GET /control` reports a device recording it |
| An index's curve | `GET /indices?since=&until=` over the days shown, the index picked above the calendar (`agent` when there is one, else the first in `names`); the bucket means averaged over three buckets, broken where a bucket is missing or has no value, drawn as an area from the block's left edge |
| A moment's pin | each of a session's `moments` on its day, in its type's colour; the chips above the calendar hide a type everywhere |
| The ribbon | the open action's day: the blocks and pins of the sessions listed that touch it, now, and a mark at the action |
| A replay | the action's inputs folded into items (`src/renderer/activity.mjs`), one stop per item at its offset from the screenshot; a click or scroll is drawn where it happened, in the screenshot's own pixels, keys as a bubble; play steps through them |
| The frames | the events of the action's sequence and of one sequence either side, a divider between sequences, the open one marked; a frame opens its action, the arrow keys step through the session's events |
| A conversation's button | each distinct `conversation` among the event's `moments`, drawn when the event's `device_id` is this computer's (`resolve_device_id`, asked of the checkout's Python at startup); on another device's event, a line naming that device. Pressing it runs `claude --resume <session_id>` in a new Ghostty window in `cwd` (`src/main/terminal.js`), without the variables of a Claude Code the app was started from |
| An input's time | its `timestamp` minus its event's `timestamp` |
| A run | `GET /predictions?limit=100` rows grouped by `run`; its time read from the run's name (`YYYYMMDD_HHMMSS`); mean reward over the rows with a reward; failed rows are those with an `error` |
| Model names | `predictor.model` and `grader.model` without the provider before the first `/` |
| Action lists | the `<action>` elements of `prediction` and `ground_truth`; green where both hold the same text at the same position |

A screenshot is requested only when its event is opened; a prediction's
prompts and the predictor's screenshots (`GET /predictions/{id}`) only when the
prediction is opened, the screenshots only when their section is expanded.
With `JUNO_TIMING_FILE` set, the main process (`src/main/server.js`) appends one JSON line per GET to that file: the route, milliseconds to the headers and to the parsed body, the server's `Server-Timing` milliseconds and the bytes.
Setup and run commands are in the [README](../README.md).

## Recorder

The client starts the recorder, `python -m capture` run with the checkout's `.venv/bin/python`
from the checkout root (so it reads the same `config.yaml` and `.env`), when it starts, and
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

## Server address

The window reads from the address saved in the settings panel, kept in
`settings.json` in Electron's userData directory (`src/main/settings.js`;
`~/.config/juno-client` on Linux, `~/Library/Application Support/juno-client`
on macOS). With none saved it is `http://<JUNO_SERVER_HOST>:8765`, the host
read from `.env` at the checkout root (see `.env.example`; default
`127.0.0.1`), which `JUNO_SERVER_URL` overrides. Saving reloads the window. The
recorder uploads to the server named in `.env` whatever the panel says.

## Passive agent module

`JUNO_PASSIVE_AGENT=1` in `.env` turns on the passive agent module: the camera
preview with hand keypoints and the gesture roles (`src/renderer/hand.js`), and
the dictation helper (`native/juno-dictate`, built by `npm run native`, macOS
only) that the dictate role feeds the message box from. The gesture model is
`models/gesture_recognizer.task`, downloaded by `npm run model`. The main
process starts the helper only when the switch is on and tells the pages
through `window.juno.passiveAgent`; off, or unset, the pages show the composer
only and start neither the camera nor the helper.
