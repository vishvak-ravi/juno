# Decisions

Newest first. Each entry is dated; later entries may supersede earlier ones.

## 2026-10-23: Proposals replace predictions in the clients

The clients' third view shows Dawn's proposed agent actions (`/proposals`),
not Tada's graded next-action predictions: the requirements' feed (F4) with
accept and reject, and its feedback log (F6) from which the accept rate (K1)
is read. `GET /predictions` and `POST /predictions` stay as routes for the
benchmark scripts; no client draws them. The user-facing unit is a
"proposal", one agent action the proposer wrote; "action" stays the
timeline's word for a captioned user action, and "task" is Dawn's.

A proposal's state is never stored on its own: it is read from its trail, the
append-only `decisions.jsonl` of its run (`lattice.proposals.state_of`). The
last of accepted, dismissed or snoozed decides it; `reopened` undoes the
decisions before it; a proposal opened but undecided is seen. A no is
**almost** when it came after the action was edited or after
`proposals.almost_seconds` of reading in total, so the feed can show what
nearly landed. A task under `dawn.utility_threshold` by at most
`proposals.near_threshold` is **almost asked**: shown under the line, with
`POST /proposals/runs/{run}/tasks/{n}` to run the proposer for it anyway
(`asked_by: user`). Shown counts the proposals a list has returned once
(`listed`, written by the server on the first page that holds the row), so
K1 is accepted over shown (`GET /proposals/rate`).

Two things Dawn computes and the paper's output does not keep are kept:
every insight's rerank score per task (the paper's top two are `given`),
and the candidates the proposer ideated and cut (`considered`, asked for in
the reply format, which the paper leaves open). Runs are stored under
`<data dir>/proposals/<run id>/`, no longer under the lattice build; the
server proposes for the day before at `proposals.propose_at`, as the
requirements allow batch work (N5). Executing an accepted proposal is still
not built.

## 2026-10-23: Conversations are events; the lattice reads typed events

The record holds events of the types the system owns (`juno.events.EVENT_TYPES`):
`activity`, a recorded session's row with its caption, and `message`, one turn of
a conversation with an assistant (`juno.conversations.Conversation`: harness,
thread id, where it ran, and its turns, each a role, a text and a time). The
server keeps each thread as one file,
`<device dir>/conversations/<harness>/<session id>.json`, read and written
through `server.conversations.Conversations`.
`scripts/import_conversations.py` fills it from a harness's own files with that
harness's reader in `capture.chats` (Claude Code: the transcripts under
`~/.claude/projects`, kept by the same rule as the hook route, `cli` and
`claude-desktop` records that are not a subagent's, Claude Code's own notes
dropped); saving replaces the thread, so running it again is a sync.

The lattice build reads the event types in `lattice.events`, each through its
own reader into one `lattice.build.Interaction`, as the `latticing` library does
(its Observer never sees the source; `examples/tada.py` and
`examples/chat.ipynb` differ only in the reader and the session rule). Activity
sessions are cut at `session_gap_hours`; each conversation is one session, the
paper's rule for conversational data (§3.2.4); a turn reads `<user name>: <text>`
or `Claude Code: <text>`, cut at `lattice.turn_chars`; the sessions of both kinds
are ordered by their first interaction, and the time layer joins their
observations. `lattice.description` names both kinds.

This stores the text of the conversations the user imports, which the
2026-10-22 hook entry avoided for live prompts; the hook still stores none. The
timeline does not list messages yet, and no moment is drawn from an imported
turn: that comes with the hook's sync of text.

## 2026-10-23: The Claude desktop app's prompts are kept too

`POST /hooks/claude-code` keeps a prompt whose `X-Claude-Code-Entrypoint` is
`cli` or `claude-desktop` (`juno.claude_hook.KEPT_ENTRYPOINTS`): Claude Code in
a terminal, and the Code tab of the Claude desktop app, whose sessions run on
the device under the same `~/.claude` as the terminal's. The hook sends a
third header, `X-Claude-Code-Host-Session`, from `CLAUDE_CODE_HOST_SESSION_ID`:
the desktop app's own id of the session (`local_…`), which is not always
`local_` plus Claude Code's session id (a session the app resumed or forked
keeps its id while Claude Code's changes). A desktop-app prompt without that
header is not kept. Each kept prompt is stored with its entrypoint and that id,
and the moment's `conversation` carries both, so the client can open each
where it ran: a terminal's by `claude --resume <session_id>` in a new terminal
in `cwd`, the desktop app's by the app's own link,
`claude://code/continue?session=<host_session_id>` (its handler takes `last` or
a `local_…` id). The client shows one chip per conversation, with Claude's
mark, the assistant's name and where it ran. IDE extensions, the SDK, cloud
sessions and subagents stay out: none has a link back from the device.


`lattice/dawn.py` and `scripts/propose_actions.py` follow Dawn (Zhao et al.,
arXiv 2604.07629, Sec. 4.1.2 and Appendix A.6-A.9) for one day of captions:
the day's action captions are the paper's list of low-level actions (its VLM
reads windows of ten screenshots, which is one of our chunks); the task model
turns them into the day's high-level tasks (A.8); the utility model scores each
task 0-1 for how differently a personal agent knowing the lattice's top-layer
insights would handle it (A.9) and tasks under `dawn.utility_threshold` (the
paper's 0.75) are dropped; a cross encoder ranks the insights against each
kept task and the best `dawn.insights_per_task` (the paper's two) go with the
task and the agent's constraints (A.7) to the action model, which proposes two
actions (A.6). The prompts are the paper's as its appendix prints them, with
`{user_name}` where it prints `USER`; the paper prints no reply format, so each
step is asked for the JSON in the `*Reply` models of `lattice/dawn.py`.

Two accommodations for a model with a short context: the day is read in
windows of `dawn.actions_per_window` captions and the tasks of every window are
put to the task model once more as the list of goals A.8 asks it to choose
from (0 reads the day whole, as the paper does); and `dawn.rerank_model` is
either the paper's Voyage AI cross encoder (`voyage/rerank-2.5`, key
`VOYAGE_API_KEY`) or `llm`, the utility model scoring each insight's relevance
to the task on its own. Executing the actions (the paper's research and
execution agents) is not built; a day's result is written next to its lattice
under `actions/<day>.json`.

## 2026-10-23: Behavior lattices with the library's prompts and method verbatim

The `lattice` package builds Behavior Lattices (Zhao et al., arXiv 2604.07629)
over the activity captions the way the `latticing` library's own Tada example
does: one interaction per action caption, its text the action's caption followed
by the chunk's dense caption, its time the action's first frame's screenshot
time; sessions cut at `lattice.session_gap_hours`; the observer reads windows of
`lattice.window_size` interactions; layer 1 groups the observations by the first
`lattice.layers` entry and runs the library's three stages (insight prose, JSON
formatting, evidence mapping, whose ids are the edges); each further layer
synthesizes the insights below it, its edges the model's `merged` ids. The five
prompts in `lattice/prompts.py` are the library's character for character,
filled as the library fills them, the `$` left before `{insights}` and
`{user_name}` included; the JSON repair in `lattice/parse.py` is the library's
`utils`. Nothing is added to the method: no signals Tada's captions lack, no
prompt edits for the local model. The one departure is which model formats
insight prose: the library sends `FORMAT_INSIGHT_PROMPT` to its insight model;
Juno sends it to `lattice.format_model`, the role `config.yaml` documents.

The roles (observer, insight, evidence, format) are litellm model strings in
`lattice` in `config.yaml`, sent as `annotate.model` is sent; every role points
at the local vLLM Gemma server. Builds are kept under `<data dir>/lattice/<build
id>/` (`lattice/store.py`): `build.json` from the moment a build starts, updated
as each stage ends with its node count, calls, failures and seconds, and
`lattice.json` once done. `scripts/build_lattice.py` and `POST /lattice` run the
same `lattice.build.build_lattice`.

## 2026-10-23: Behavior lattices with the library's prompts and method verbatim

The `lattice` package builds Behavior Lattices (Zhao et al., arXiv
2604.07629) over the activity captions the way the `latticing` library's own
Tada example does: `lattice/prompts.py` holds the library's five prompts
character for character, filled with `str.format` as the library fills them
(the `$` the library leaves in front of the prose and the user's name is
sent too), and `lattice/build.py` follows `Lattice.forward` stage by stage.
One interaction per action caption, its text the action's caption followed by
the chunk's dense caption, its time the action's first frame's screenshot
time; sessions cut at `lattice.session_gap_hours`; the observer reads
windows of `lattice.window_size` interactions and each of its observations
becomes two layer 0 nodes (`think_feel`, `actions`); layer 1 is prose
(`observer_model`, `insight_model`), then JSON (`format_model`), then the
evidence mapping (`evidence_model`) whose ids are the edges; higher layers are
synthesized with `insight_model` and their `merged` ids are the edges. The
group splits (`session`, `time`, `number`, `all`) are the library's
`Layers`, sorting included. Every role in `config.yaml` is the local vLLM
server's `openai/cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit` at
`lattice.api_base`; a prompt longer than its context fails that one window,
group or insight, which is counted in the build's `stage_failures` and left
out. Lattices live under `<data dir>/lattice/<build id>/` (`build.json`,
`lattice.json`), built by `scripts/build_lattice.py` or `POST /lattice`.

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
