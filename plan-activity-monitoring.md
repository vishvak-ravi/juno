# Plan for activity monitoring

Status 2026-10-01: recorder and Wayland backend written. Screen capture verified on
this machine. Real input needs one `usermod` before the first full run.

**Success:** I use my computer normally, and what I do is logged as events on
disk, exactly as Tada's `napsack` recorder would log it. Nothing else counts:
no VLM captions, no memory wiki, no chat, no Electron app. Those are later
steps that consume these events.

Stick to Tada's behavior. No additions such as active-window tracking or idle
markers.

## 1. What "logged as events" means

The session napsack writes, under `<data>/screen/sessions/session_<YYYYmmdd_HHMMSS>/`:

```
events.jsonl             every input event (napsack's own log, Python repr lines)
input_events.jsonl       the same events as JSON
raw_aggregations.jsonl   one record per burst boundary: timestamp, reason, event_type,
                         request_state, screenshot_path, screenshot_timestamp,
                         end_screenshot_timestamp, num_events, events[], cursor_position,
                         monitor, burst_id, scale_factor
screenshots/*.png        one frame per aggregation
screenshots.jsonl        {timestamp, path, monitor_index}
```

Each event: `{timestamp, monitor_index, monitor, event_type, details, cursor_position}`
with `event_type` in `mouse_down | mouse_up | mouse_scroll | key_press | key_release`.
Tada's settings: 5 fps frame buffer, 120 s window, screenshots scaled to
100 dpi, mouse moves dropped, napsack's "accurate" burst thresholds.

Acceptance test: record an hour of real work, then `python -m capture.check`
passes. It fails on no events, no aggregations, missing or black screenshots,
or events that landed in no aggregation. In addition, no portal dialog may
appear after the first run.

## 2. Why Tada's recorder fails here and what replaces it

Machine: Ubuntu 26.04, GNOME Shell 50.1 on Wayland, one 4K monitor at 125%
scaling (3072x1728 logical, 3840x2160 pixels).

napsack's queues, burst aggregation and save code are platform neutral and
import without `pynput` or `mss` (verified). Only its two handlers are bound
to X11, so only those are replaced:

| napsack piece | Fails because | Interface | Wayland implementation | State |
|---|---|---|---|---|
| `ScreenshotHandler` (`mss`) | X11 only, black frames on Wayland | `ScreenSource` | `PortalScreenSource`: portal ScreenCast, `persist_mode=2` + restore token, PipeWire frames via GStreamer in a system-Python helper | verified here: one dialog on first run, none on the second, 5 fps, 3840x2160, not black |
| `InputEventHandler` + listeners (`pynput`) | X11 hooks; Wayland-native apps invisible | `InputSource` | `EvdevInputSource`: `/dev/input/event*`, key codes translated to pynput names (US layout) | written and unit tested; spike confirmed by hand; needs the `input` group for a full run |
| `mouse.Controller().position` | Wayland has no global pointer position | `CursorSource` | `UnknownCursor`: always None | events carry `cursor_position: null`; pointer is still drawn into each frame |

The Screenshot-portal alternative was not needed: ScreenCast works.

X11 backend = Tada's path (`mss` + `pynput`) behind the same interfaces. Mac =
napsack as is. Both later.

## 3. What exists (`app/`)

```
app/src/capture/
  sources/base.py           ScreenSource, InputSource, CursorSource, Monitor, Frame, Backend
  sources/__init__.py       backend registry + detection from XDG_SESSION_TYPE
  sources/wayland.py        create(): portal screen + evdev input + unknown cursor
  sources/portal_screen.py  starts the helper, keeps the newest frame per monitor, restarts a dropped session
  sources/portal_helper.py  runs under /usr/bin/python3: portal handshake, PipeWire -> RGB frames on stdout
  sources/evdev_input.py    reads /dev/input, hot-plug rescan
  sources/evdev_keys.py     evdev key codes -> pynput names
  sources/fake.py           generated frames + scripted input, for tests and demos
  sources/x11.py            STUB
  handlers.py               InputHandler, ScreenshotLoop (replace napsack's two handlers)
  recorder.py               Recorder: napsack queues + workers wired to a Backend
  __main__.py               python -m capture [--backend auto|wayland|x11|fake]
  check.py                  python -m capture.check (the acceptance test)
app/spikes/evdev_input.py   prints live evdev events next to the names that get logged
app/tests/                  109 tests
```

Verified: with real screen frames and scripted input, a session is written in
napsack's layout, every event lands in an aggregation, the checker passes, and
the output parses with napsack's `sanitize_records` and label models (what
Tada's labeler consumes).

Not yet verified: one run with real screen and real input together. I cannot
read `/dev/input` from here.

## 4. Remaining steps

1. **Input access (you, once):**
   ```
   sudo usermod -aG input $USER
   ```
   then log out and back in, or use `sg input -c '...'` until you do.
2. **First real run:**
   ```
   cd ~/dev/juno/app
   uv run python -m capture          # or: sg input -c 'uv run python -m capture'
   uv run python -m capture.check    # after Ctrl+C
   ```
3. **Cost of Tada's settings on a 4K screen** (measured while the screen was
   changing constantly): about 77% of one core, because every changed frame
   is resized; memory grows toward 12 GB, because the 120 s buffer holds 600
   frames of 20 MB; each saved screenshot is 1.5 to 3.5 MB at 3445x1938.
   A static screen costs almost nothing. `--scale 0.5 --buffer-seconds 10`
   brings this down to 1920x1080 screenshots and well under 1 GB. napsack only
   needs a few seconds of buffer, so 10 s loses nothing. Decide whether to
   change the defaults.
4. **Hour-long acceptance run**, then a day, watching disk, memory and CPU.
5. **Lock and unlock** once while recording, to confirm the session restarts
   without a dialog. The restart path is tested only with a fake helper.
6. **Pointer position**, if click coordinates turn out to matter for labels.
   Would need a compositor-specific source.
7. **Run at login** (systemd user unit).

Handoff to the labeling work: sessions are under
`~/.local/share/juno/screen/sessions/session_<timestamp>/`. For live
labeling, `Recorder(on_aggregation=...)` hands over each processed
aggregation with its frame still in memory, which is what Tada's
`Labeler.label_chunk` takes.

## 5. Deferred (explicitly out of scope for success)

VLM captioning of the sessions (Tada's `labeler.py` against local vLLM; the
`on_aggregation` hook on `Recorder` is where it plugs in), memory wiki, chat,
`juno` CLI integration, Electron app, every other context source
(notifications, filesystem, audio, email, calendar), Mac and X11 backends,
systemd unit, packaging.

## 6. Decided

- Key text is logged in full, as Tada does. Passwords are included.
- No active-window or idle tracking. Tada has neither.
