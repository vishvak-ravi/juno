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
| Timeline | one day, the day picked from a calendar shaded by activity, in one of four views picked by a switch above it and remembered on the computer: **Activity**, what the user did, newest first, a time and a line per item coloured by its kind, marked by hour and by moment; **Linear**, every device's events on one line, newest first, marked by hour and by moment, with a rail of the day's moments folded into the pane's left edge and a minimap of the day's indices down its right edge, pressed to jump to a time; **Sessions**, the day's sessions, newest first, a session fetching its events when opened; **Indices**, each index through the day in its own lane, read at the pointer | one event: caption, screenshot, inputs; a bar below it steps to the earlier and later event of the session, or of the activity or linear view's rows fetched, also on the arrow keys; an activity item opens the event it began in |
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
| A day's tint in the calendar | `GET /events/activity?edges=` with the local midnights of the six weeks shown; the square root of the day's count over the busiest day in view |
| A day, linear | `GET /events?since=&until=&order=desc&limit=100`, since and until the local midnights around it, and `GET /indices?since=&until=` for the minimap, fetched together; the next page is fetched when "Earlier events" scrolls into view. Pressing the minimap at a time outside the rows fetched refetches around it: `GET /events?until=<time>&order=desc` and `GET /events?since=<time>&order=asc`, then "Later events" above the rows and "Earlier events" below them each fetch their next page when scrolled into view; the rows stay one unbroken run of the day |
| A day, activity | the same pages, every row fetched so far folded again as each page arrives (`src/renderer/activity.mjs`): each device's inputs in time order, pointer moves and releases making no item |
| Typed text | characters and spaces pressed with no modifier but Shift (the character comes shifted), Backspace taking off the last one; it goes on past scrolls and ends at Enter (marked ⏎), a pause of `GAPS.typing` seconds, a click or any other named key but Backspace |
| A shortcut | a key pressed while Ctrl, Alt or Cmd is held, or Shift with a named key; the same one again within `GAPS.keys` counts up; a modifier pressed more than `GAPS.modifier` seconds before with no key since is taken as released |
| Keys | named keys (arrows, Esc, Tab, …) within `GAPS.keys` of each other, repeats counted; Cmd or Alt pressed and released alone is a key |
| A click | each press of a button; two of one button within `GAPS.click` are a double click |
| A scroll | scroll steps within `GAPS.scroll` of each other, summed per direction; `dy > 0` is up, as napsack reads it |
| An hour mark | wherever the local hour of an event differs from the one above it |
| A moment | a divider above the event of each of its `moments`, under that event's hour mark; its text from the moment's `type` and the assistant in its `label`, its diamond in the type's colour |
| The moment rail | one line per divider, in the list's order, coloured by the moment's `type`; a 3px sliver at the pane's left edge that opens to 28px when the pointer reaches it. Lines are spaced evenly, 5px to 14px apart; a shaded band spans the lines whose dividers are in view. Hovering a line grows it and its two neighbours a step less and shows the moment's text and time; pressing it scrolls the list to the divider, lit for 1.5s |
| The index minimap | the day down the pane's right edge, 22px wide, the day's end at the top, in place of the list's scrollbar; a column per name in `names` of `GET /indices`, a cell per bucket with a value, its darkness the value; nothing shown when no bucket was answered. A thumb spans the rows in view; a mark in the open event's device tone sits at the open event. The pointer over the strip reads the time and every index of the bucket there; pressing opens the nearest event, in the middle of the view; the arrow keys move the reading a bucket and Enter jumps to it |
| A day, indices | `GET /indices?since=&until=`, the day's buckets of `keypoints.bucket_seconds`; a lane per name in `names`, the line broken where a bucket is missing or has no value; nothing drawn when no bucket was answered |
| A device's colour | its place among the devices of `GET /control` in id order, then devices not there as they are drawn; one of six |
| A day, sessions | `GET /sessions?since=&until=&limit=100`: each session with an event that day, its start, end, event count and captioned count |
| A session's events | `GET /sessions/{id}`, the first time the session is opened |
| A session's light | green when `GET /control` reports a device recording that session |
| An event's chips | its `inputs` counted by type: key presses, clicks, scrolls; key and button releases and pointer moves are left out of the chips and listed folded in the event's detail |
| An input's time | its `timestamp` minus its event's `timestamp` |
| A run | `GET /predictions?limit=100` rows grouped by `run`; its time read from the run's name (`YYYYMMDD_HHMMSS`); mean reward over the rows with a reward; failed rows are those with an `error` |
| Model names | `predictor.model` and `grader.model` without the provider before the first `/` |
| Action lists | the `<action>` elements of `prediction` and `ground_truth`; green where both hold the same text at the same position |

A screenshot is requested only when its event is opened; a prediction's
prompts and the predictor's screenshots (`GET /predictions/{id}`) only when the
prediction is opened, the screenshots only when their section is expanded.
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
