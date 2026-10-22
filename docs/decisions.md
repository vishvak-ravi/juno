# Decisions

Newest first. Each entry is dated; later entries may supersede earlier ones.

## 2026-10-21: Jev indexes the timeline

An exploration, outside "Capture follows Tada": Tada has no indices or
moments. After a chunk is annotated, every row of it is one request to Jev
(TypeSafe's System One model, `keypoints.model`): the row's screenshot with
the pointer drawn in, its input text and its caption, against the questions in
`packages/server/src/keypoints/questions.json`. A row with no key press, click
or scroll is asked the `screen` questions only. The answers are stored per
chunk under `<session>/detections/jev/`, with the model and a hash of the
questions file; indices and moments are worked out from them when read
(`keypoints.derive`), so `moment_on`/`moment_off` change what is shown without
asking again. Changing `questions.json` does not re-ask stored rows; deleting a
session's `detections/jev/` does.

Every question goes in every row's request for now, as a shotgun: the views
(dividers in the linear timeline, the indices chart) are for finding out which
answers are worth keeping. Screenshots go to TypeSafe as they are, which the
"Dreamer-ready" image support of the API accepts but does not promise to judge
well. Sanitization before sending is out of scope here.

`engagement` and `friction` from the first drafts are left out: which inputs a
row has is in its events, and an error on screen is its own Noul
(`error_shown`).

## 2026-10-21: The annotator is a litellm model string and an address

`annotate.model` is any litellm model string and `annotate.api_base` the server
an `openai/` model is sent to; switching between Gemini, OpenRouter, llama.cpp,
vLLM and SGLang changes only those two values (and a key in `.env` for a
hosted provider). Every model except `gemini/` is sent Tada's
`DENSE_IMAGE_CAPTION_SCHEMA` as an OpenAI `json_schema` response format.
napsack instead sends `json_object` to such models and `guided_json` to
`hosted_vllm/` ones; neither llama.cpp's server nor the vLLM build in use
reads `guided_json`, and on 2026-10-21 Qwen3.8-27B on llama-server answered
with a fenced JSON block that does not parse. A `gemini/` model keeps
napsack's arguments, so the parity test against Tada's labeler still holds.

Tried on 2026-10-21: Gemini (`gemini/gemini-3.1-flash-lite`) and llama.cpp
(`openai/Qwen3.8-27B-UD-Q4_K_M`, 138 chunks). Not yet run: vLLM, SGLang,
OpenRouter. Runs of a local annotator go through
`scripts/annotate_session_activity.py` into `data/` at the checkout root, not
the server's store, so they sit next to the Gemini annotations without
replacing them.

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
