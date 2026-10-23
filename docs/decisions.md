# Decisions

Newest first. Each entry is dated; later entries may supersede earlier ones.

## 2026-10-22: Inputs regrouped into their rows when rows are read

napsack's `EventQueue._link_requests_with_timestamps` releases the last pending
request as soon as every burst queue is empty, with no
`end_screenshot_timestamp`; `AggregationWorker.process_aggregation` then gives
that row every input from its screenshot onward, including the next burst's,
whose own row starts empty. In `session_20261022_184125`, 190 of 623 rows were
written that way and 1416 of 2500 inputs sat in the row before their own.
Tada's recorder (`napsack/record/__main__.py`) builds the same queue, and its
labeler reads rows as stored, so Tada's labels carry the same misplacement.

Decision: keep the recorder identical to Tada's and correct the rows when they
are read. `juno.sessions.parse_rows`, which both `read_aggregations` and
`server.events.read_rows` go through, gives each input to the row whose
screenshot precedes it, rows taken in screenshot order: napsack's own window
(`screenshot <= input < next screenshot`), computed once every row exists.
Files on disk and row ids do not change, and sessions already recorded are
corrected on read. Because a chunk's last row is settled by the row after it,
`PUT /events` captions a chunk when the first row of the next chunk is stored,
and `find_work` treats a session's last chunk, full or not, as a tail that
waits `annotate.tail_idle_seconds`. Captions made before this were built from
the misgrouped rows and are left as they are; `covers` counts them as done.
Rejected: deleting the "last request" branch in napsack, which would change our
recordings' shape relative to Tada's 0.1.3 and leave existing sessions wrong.

## 2026-10-22: The server's store behind repositories

Routes and workers read and write the store only through `Events`,
`ActivityCaptions`, `QuestionAnswers` and `Control` (see `capture.md`), each
with a file implementation, so a database can replace one without touching its
callers. The stored names say what they hold: the model's chunk captions are
activity captions (`activity_captions/`, was `annotations/`), Jev's answers
are question answers (`question_answers/`, was `detections/jev/`), and the
server's copy of the rows is `event_rows.jsonl`; the recorder still writes Tada's
`raw_aggregations.jsonl`. Screenshot paths are stored relative to the session,
and `scripts/migrate_store.py` moves an older store. The `annotate` and
`keypoints` packages and the `annotate:`/`keypoints:` sections of
`config.yaml` keep their names.

## 2026-10-22: Claude Code reports its own prompts

Outside "Capture follows Tada", like Jev's moments. Claude Code's
UserPromptSubmit hook, of the `http` type, posts each prompt's hook input to
`POST /hooks/claude-code?device=<id>`; `python -m capture.claude_hook install`
writes it into `~/.claude/settings.json` with the address and device id the
recorder uploads with. The body says nothing of where Claude Code runs, so the
hook interpolates `CLAUDE_CODE_ENTRYPOINT` and `CLAUDE_CODE_REMOTE` into two
headers. The server keeps the session id and cwd, never the prompt's text
(the recorder already has the keystrokes), stamped with its own clock on
arrival; at read time the prompt is an `agent_prompt_submitted` moment on the
device's row whose span holds that time, replacing Jev's guess of that type on
that row, and carries `conversation` so the client can run
`claude --resume <session id>` in a new Ghostty window in that cwd.

The route keeps only prompts typed into Claude Code in a terminal
(`entrypoint` `cli`, not remote, not a subagent), the one origin the button
can reopen; `server.routes.hooks.unsupported` names each origin left out. A
cloud session's conversation and cwd are not on the device; the Claude
desktop app, IDE extensions and the SDK (`claude -p`) reopen their sessions in
their own window, or are scripts; claude.ai and Claude app chats run no hooks.
Each of these needs its own way back to the conversation before it is kept.
The route answers 204 with no body whatever it decides, and the hook waits
`claude_hook.timeout_seconds` at most, so a slow or absent server delays a
prompt by that much and never blocks it.

A prompt sent while nothing was recorded on its device is stored and shown
on no event.

## 2026-10-22: The MCP is the record, three tools over it

An agent connected over MCP gets Juno's record and works out from it what the
user is referring to; it looks further with its own tools (files, shell, web).
The record is devices, their sessions, and the sessions' events, with each
event's caption, text on screen, indices, moments and screenshot. The tools
follow that nesting: `list_sessions` (what each device did when, with moment
counts and mean indices), `find_events` (runs of same-caption events, by
words on screen, moment, session, device or time) and `get_event` (one event
in full with its screenshot as an MCP image, or a device's newest). They
replace the per-chunk `list_annotations` and `get_annotation`;
`predict_next_actions` is dropped, and `POST /predictions` stays. Juno's chat
model gets the same three and is sent text only.

Shaped by Anthropic's "Writing effective tools for agents" (few tools built
for tasks, readable fields, results capped with a way to get more, errors that
name the fix), Block's "Playbook for designing MCP servers" (workflow-first,
one risk level per tool) and the MCP 2025-11-25 tools spec (image content,
`isError` results).

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
