# Decisions

Newest first. Each entry is dated; later entries may supersede earlier ones.

## 2026-10-13: Tablet drawing recorded by the server

A tablet can't run `python -m capture`, so `/draw` (a black canvas, four
colours, draw and erase) streams its canvas and pointer events over a socket
and the server runs the same `Recorder` for it on a pushed-input backend
(`capture.sources.pushed`). Its sessions go through `PUT /events` like any
recorder's, under the device `draw.device_id`. Pencil and finger strokes are
recorded as `Button.left` presses; taps on the toolbar are not recorded and the
toolbar is not in the frames. This is a prototype of the iPad as an event
writer, which `requirements.md` lists as cut.

## 2026-10-12: Switches in a file on the server

Whether each device records, and whether the server annotates, is kept in
`<data_dir>/control.json` on the server, not in `config.yaml`. The server is the
only process that writes it; other devices read and change it through
`/control`, and recorders poll it. This is v1; a database and proper
distributed state come later. Annotation has one switch, the server's, because
the queue runs there; devices have only a recording switch.

## 2026-10-12: Annotation model

Annotation (`annotate.model`) runs on `gemini/gemini-3.1-flash-lite` on the
Gemini free tier: no charge, but Google may train on and human-review what is
sent (screenshots included), which I accept for now. The limits are 15 RPM,
250k TPM and 500 requests/day, and I have hit the daily cap. A grader for the
intent model would use the same model and key (Tada's default), so it shares
that quota. Eventually (important, not urgent): move annotation and grading to a
local model, after evaluating grader quality and annotation quality with the
criteria Tada's paper uses for each.

## 2026-10-01: Capture follows Tada exactly

- Stick to Tada's (`napsack`'s) behavior. No additions such as active-window
  tracking or idle markers; Tada has neither.
- Key text is logged in full, as Tada does. Passwords are included.

Plan at the time: [`history/2026-10-01-activity-monitoring-plan.md`](history/2026-10-01-activity-monitoring-plan.md).
