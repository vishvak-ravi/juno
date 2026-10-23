// What the desktop browser makes of the routes' rows, without drawing anything:
// sessions laid out on the calendar and down the moment rail, a session's events
// grouped into sequences, an index smoothed into a curve, inputs read as lines,
// graded predictions grouped into runs and their <action> lists read, devices
// reduced to one state. Every value here is computed from rows already fetched;
// nothing is requested.

// What a moment's divider says, by its type and its label (the assistant in focus).
const ASSISTANTS = { claude_code: "Claude Code", codex: "Codex", chatgpt: "ChatGPT", hermes: "Hermes", juno_chat: "Juno chat" };
const MOMENTS = {
  agent_prompt_submitted: (who) => (who ? `Prompt sent to ${who}` : "Prompt sent"),
  code_landed: () => "Code committed or merged",
  message_sent: () => "Message sent",
  web_search_submitted: () => "Web search",
  agent_needs_input: (who) => `${who ?? "Assistant"} waiting for your answer`,
  error_shown: () => "Error on screen",
};
// The moment types the calendar's chips offer, in the order they are shown.
export const MOMENT_TYPES = Object.keys(MOMENTS);
export function momentText(moment) {
  const text = MOMENTS[moment.type];
  const who = ASSISTANTS[moment.label] ?? null;
  return text ? text(who) : moment.type.replaceAll("_", " ");
}

// The conversations an event's moments carry (those an assistant's hook
// reported), each once, in the order of its moments.
export function conversationsOf(event) {
  const seen = new Map();
  for (const { conversation } of event.moments ?? []) {
    const key = conversation && `${conversation.harness} ${conversation.session_id}`;
    if (key && !seen.has(key)) seen.set(key, conversation);
  }
  return [...seen.values()];
}

// A session's events (oldest first) grouped into its sequences, the rows the
// model captioned together: `{ index, caption, events }`, oldest first.
export function sequencesOf(events) {
  const sequences = new Map();
  for (const event of events) {
    if (!sequences.has(event.sequence)) sequences.set(event.sequence, { index: event.sequence, caption: event.sequence_caption, events: [] });
    sequences.get(event.sequence).events.push(event);
  }
  return [...sequences.values()];
}

// The moment rail's lines for `sessions` (newest first), in the list's order:
// each session's moments newest first, and `{ divider: true }` where the local
// day changes between sessions.
export function railLines(sessions) {
  const lines = [];
  let last = null;
  for (const session of sessions) {
    const start = day(new Date(session.start * 1000)).since;
    if (last != null && start !== last) lines.push({ divider: true });
    last = start;
    for (const moment of session.moments.toReversed()) lines.push({ moment, session });
  }
  return lines;
}

// Moments (oldest first) drawn as pins: those within `gap` seconds of a pin's
// first moment share it, `{ timestamp, moments, type }`, `type` the one most of
// its moments have (the first's on a tie).
export function pinClusters(moments, gap) {
  const pins = [];
  for (const moment of moments) {
    const last = pins.at(-1);
    if (last && moment.timestamp - last.timestamp <= gap) last.moments.push(moment);
    else pins.push({ timestamp: moment.timestamp, moments: [moment], type: moment.type });
  }
  for (const pin of pins) {
    const counts = new Map();
    for (const { type } of pin.moments) counts.set(type, (counts.get(type) ?? 0) + 1);
    pin.type = [...counts].reduce((best, entry) => (entry[1] > best[1] ? entry : best))[0];
  }
  return pins;
}

// The local day holding `date`, as GET /events' since and until.
export function day(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const end = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1);
  return { since: start.getTime() / 1000, until: end.getTime() / 1000 - 0.001, start };
}
// "Today", "Yesterday", else the weekday and date of the day starting at `start`.
export function dayLabel(start, now = new Date()) {
  const back = Math.round((day(now).start - start) / 86400000);
  return back === 0 ? "Today" : back === 1 ? "Yesterday"
    : start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
// The `count` days of the calendar from `first` (a local midnight), as local midnights.
export const daysFrom = (first, count) => Array.from({ length: count }, (_, i) =>
  new Date(first.getFullYear(), first.getMonth(), first.getDate() + i));
// The first day of the week holding `date`, `weekStart` 0 Sunday … 6 Saturday.
export function weekOf(date, weekStart = 0) {
  const back = (date.getDay() - weekStart + 7) % 7;
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() - back);
}
// A session on the days it touches: per day of `days` (local midnights), the
// fraction of the day it runs from and to, `{ day, from, to }`.
export function dayBlocks(session, days) {
  return days.flatMap((date, index) => {
    const { since, until } = day(date);
    const from = Math.max(session.start, since);
    const to = Math.min(session.end, until);
    return from <= to ? [{ day: index, from: (from - since) / 86400, to: (to - since) / 86400 }] : [];
  });
}
// The six weeks a month's calendar shows, as 42 local midnights from the first
// `weekStart` (0 Sunday … 6 Saturday) on or before the first of the month.
export function monthGrid(year, month, weekStart = 0) {
  const lead = (new Date(year, month, 1).getDay() - weekStart + 7) % 7;
  return Array.from({ length: 42 }, (_, i) => new Date(year, month, 1 - lead + i));
}
// The grid's days as GET /events/activity edges: each midnight and the one after the last day.
export const gridEdges = (days) => [...days, new Date(days[days.length - 1].getFullYear(), days[days.length - 1].getMonth(),
                                                     days[days.length - 1].getDate() + 1)].map((d) => d.getTime() / 1000);
// How dark a day is drawn, 0 to 1, by its count against the busiest day shown;
// square-rooted so a quiet day still shows beside a busy one.
export const shade = (count, max) => (count > 0 && max > 0 ? Math.sqrt(count / max) : 0);
// The first day of the week in `locale`, 0 Sunday … 6 Saturday; Sunday where unknown.
export function weekStart(locale) {
  try {
    const info = new Intl.Locale(locale);
    return ((info.getWeekInfo?.() ?? info.weekInfo).firstDay) % 7;
  } catch {
    return 0;
  }
}
// The seven weekday initials from `start`, in `locale`.
export const weekdays = (start, locale) => Array.from({ length: 7 }, (_, i) =>
  new Date(2024, 0, 7 + ((start + i) % 7)).toLocaleDateString(locale, { weekday: "narrow" }));

// One index of GET /indices between `since` and `until` as a curve: the runs
// of consecutive buckets with a value, each point `{ x, y, start }` with x the
// bucket's middle as a fraction of the span and y the value averaged with the
// `window` buckets around it (0..1). A missing bucket, or one with no value,
// ends a run, so gaps show as gaps.
export function indexCurve(indices, name, since, until, window = 3) {
  const span = until - since;
  const step = indices.bucket_seconds;
  const runs = [];
  let run = null;
  let last = null;
  for (const bucket of indices.buckets) {
    const value = bucket.values[name];
    if (value == null || (last != null && bucket.start - last > step)) run = null;
    if (value != null) {
      if (!run) runs.push(run = []);
      run.push({ x: (bucket.start + step / 2 - since) / span, y: value, start: bucket.start });
    }
    last = bucket.start;
  }
  const reach = Math.floor(window / 2);
  return runs.map((points) => points.map((point, n) => {
    const around = points.slice(Math.max(0, n - reach), n + reach + 1);
    return { ...point, y: around.reduce((sum, p) => sum + p.y, 0) / around.length };
  }));
}

// Inputs that say what the user did, by type; the rest (releases and pointer
// moves) are folded away under the ones shown.
const SHOWN = new Set(["key_press", "mouse_down", "mouse_scroll"]);
const RELEASES = new Set(["key_release", "mouse_up"]);

export const shownInputs = (inputs) => inputs.filter((i) => SHOWN.has(i.type));

// "3 releases and 11 pointer moves" for what `shownInputs` leaves out; null if nothing.
export function foldedNote(inputs) {
  const releases = inputs.filter((i) => RELEASES.has(i.type)).length;
  const moves = inputs.filter((i) => i.type === "mouse_move").length;
  const parts = [releases && `${releases} release${releases === 1 ? "" : "s"}`,
                 moves && `${moves} pointer move${moves === 1 ? "" : "s"}`].filter(Boolean);
  return parts.length ? parts.join(" and ") : null;
}

// One input as [what, details]: ["Key", "k"], ["Click", "left · 640, 400"].
// x and y are null where the pointer cannot be read.
const at = (i) => (i.x == null ? [] : [`${i.x}, ${i.y}`]);
export function inputLine(input) {
  const button = (input.button ?? "").replace(/^Button\./, "");
  switch (input.type) {
    case "key_press": return ["Key", input.key];
    case "key_release": return ["Key up", input.key];
    case "mouse_down": return ["Click", [button, ...at(input)].join(" · ")];
    case "mouse_up": return ["Release", [button, ...at(input)].join(" · ")];
    case "mouse_scroll": return ["Scroll", [`${input.dx}, ${input.dy}`, ...at(input)].join(" · ")];
    case "mouse_move": return ["Move", at(input).join("") || "–"];
    default: return [input.type, ""];
  }
}

// An input's time from its event's screenshot: "+0.21 s".
export const offset = (input, event) => {
  const s = input.timestamp - event.timestamp;
  return `${s < 0 ? "−" : "+"}${Math.abs(s).toFixed(2)} s`;
};

export const clock = (seconds, withSeconds = false) => new Date(seconds * 1000).toLocaleTimeString(undefined,
  { hour: "numeric", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}) });

// The <action> elements of a prediction or a ground truth, in order.
const ENTITIES = { "&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&apos;": "'" };
export const actions = (text) => [...(text ?? "").matchAll(/<action>([\s\S]*?)<\/action>/g)]
  .map((m) => m[1].trim().replace(/&(lt|gt|amp|quot|apos);/g, (e) => ENTITIES[e]));

// Whether position n of both lists holds the same action.
export const matches = (predicted, actual) => predicted.map((a, n) => a === actual[n]);

// A run's name is when it started, YYYYMMDD_HHMMSS, local time.
export function runTime(run) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(run);
  return m ? new Date(m[1], m[2] - 1, m[3], m[4], m[5], m[6]) : null;
}

// "hosted_vllm/local-model" → "local-model".
export const modelName = (model) => model.slice(model.indexOf("/") + 1);

const mean = (values) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);

// Graded predictions grouped by run, in the order the page lists them; each
// run's rows in prediction order.
export function groupRuns(rows) {
  const runs = new Map();
  for (const row of rows) {
    if (!runs.has(row.run)) runs.set(row.run, []);
    runs.get(row.run).push(row);
  }
  return [...runs].map(([run, graded]) => {
    graded.sort((a, b) => a.index - b.index);
    return { run, rows: graded, time: runTime(run),
             predictor: modelName(graded[0].predictor.model), grader: modelName(graded[0].grader.model),
             mean: mean(graded.map((g) => g.reward).filter((r) => r != null)),
             failed: graded.filter((g) => g.error != null).length };
  });
}

// One device of GET /control as a state for its light: "recording", "changing"
// (asked for one thing, still doing the other), "idle" or "offline".
export function deviceState(d) {
  if (!d.online) return "offline";
  const running = d.reported.capture === "running";
  if (running !== d.capture) return "changing";
  return running ? "recording" : "idle";
}

// The light on the devices button: "offline" when the server cannot be reached,
// "changing" when this computer's recorder exited or a device has not caught up,
// "recording" when any device records, else "idle".
export function overallState(control, recorder) {
  if (!control) return "offline";
  const states = Object.values(control.devices).map(deviceState);
  if ((recorder && !recorder.running) || states.includes("changing")) return "changing";
  return states.includes("recording") ? "recording" : "idle";
}

// The session a device is recording now, by session id, from GET /control.
export const liveSessions = (control) => new Set(Object.values(control?.devices ?? {})
  .filter((d) => d.online && d.reported.capture === "running").map((d) => d.reported.session_id));
