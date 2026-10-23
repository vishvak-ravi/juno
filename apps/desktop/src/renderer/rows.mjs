// What the desktop browser makes of the routes' rows, without drawing anything:
// events laid out on the linear timeline and their inputs counted, graded
// predictions grouped into runs and their <action> lists read, devices reduced
// to one state. Every value here is computed from rows already fetched; nothing
// is requested.

// The start of the local hour holding `seconds`, in Unix seconds.
export function hourOf(seconds) {
  const date = new Date(seconds * 1000);
  date.setMinutes(0, 0, 0);
  return date.getTime() / 1000;
}

// The linear timeline's rows for `events` (newest first): `{ hour }` wherever
// the local hour changes, then `{ moment, event }` for each moment starting at
// an event, then `{ event, named }` for the event, `named` where its device
// differs from the event drawn above it or a new hour begins. `above` is the
// event drawn just before `events`, null at the top.
export function railItems(events, above = null) {
  const items = [];
  let previous = above;
  for (const event of events) {
    const hour = hourOf(event.timestamp);
    const newHour = !previous || hourOf(previous.timestamp) !== hour;
    if (newHour) items.push({ hour });
    for (const moment of event.moments ?? []) items.push({ moment, event });
    items.push({ event, named: newHour || previous.device_id !== event.device_id });
    previous = event;
  }
  return items;
}

// What a moment's divider says, by its type and its label (the assistant in focus).
const ASSISTANTS = { claude_code: "Claude Code", codex: "Codex", chatgpt: "ChatGPT", hermes: "Hermes", juno_chat: "Juno chat" };
const MOMENTS = {
  agent_needs_input: (who) => `${who ?? "Assistant"} waiting for your answer`,
  agent_prompt_submitted: (who) => (who ? `Prompt sent to ${who}` : "Prompt sent"),
  error_shown: () => "Error on screen",
  code_landed: () => "Code committed or merged",
  message_sent: () => "Message sent",
  web_search_submitted: () => "Web search",
};
export function momentText(moment) {
  const text = MOMENTS[moment.type];
  const who = ASSISTANTS[moment.label] ?? null;
  return text ? text(who) : moment.type.replaceAll("_", " ");
}

// The moment rail's lines: `height` px shared out over `count` lines, no closer
// than LINE_MIN and no further than LINE_MAX apart, LINE_INSET kept clear at
// each end.
export const LINE_MIN = 5;
export const LINE_MAX = 14;
export const LINE_INSET = 8;
export function linePitch(count, height) {
  const room = Math.max(0, height - 2 * LINE_INSET);
  return Math.max(LINE_MIN, Math.min(LINE_MAX, count ? room / count : LINE_MAX));
}

// GET /indices laid out on the day from `since` to `until`: per index name, the
// runs of consecutive buckets with a value, each point `{ x, y, start, value }`
// with x the bucket's middle as a fraction of the day and y the value (0..1). A
// missing bucket, or one with no value, ends a run, so gaps show as gaps.
export function indexLanes(indices, since, until) {
  const span = until - since;
  const step = indices.bucket_seconds;
  return indices.names.map((name) => {
    const runs = [];
    let run = null;
    let last = null;
    for (const bucket of indices.buckets) {
      const value = bucket.values[name];
      if (value == null || (last != null && bucket.start - last > step)) run = null;
      if (value != null) {
        if (!run) runs.push(run = []);
        run.push({ x: (bucket.start + step / 2 - since) / span, y: value, start: bucket.start, value });
      }
      last = bucket.start;
    }
    return { name, runs };
  });
}

// GET /indices as the minimap draws it: the day from `since` to `until` down a
// strip with `until` at the top; per index name, one cell per bucket with a
// value, `{ top, height, value, start }`, top and height as fractions of the strip.
export function indexCells(indices, since, until) {
  const span = until - since;
  const step = indices.bucket_seconds;
  return indices.names.map((name) => ({ name, cells: indices.buckets
    .filter((bucket) => bucket.values[name] != null)
    .map((bucket) => ({ top: 1 - (bucket.start + step - since) / span, height: step / span,
                        value: bucket.values[name], start: bucket.start })) }));
}
// The bucket of GET /indices holding the time `seconds`; null between buckets.
export const bucketAt = (indices, seconds) => indices.buckets
  .find((bucket) => seconds >= bucket.start && seconds < bucket.start + indices.bucket_seconds) ?? null;
// The time at `fraction` down a strip that runs from `until` at the top to `since` at the bottom.
export const timeDown = (fraction, since, until) => until - fraction * (until - since);

// `rows` (newest first) with the events of `page` merged in, newest first and
// each event once, in the order GET /events uses: by timestamp, then id.
export function mergeEvents(rows, page) {
  const byId = new Map(rows.map((event) => [event.id, event]));
  for (const event of page) byId.set(event.id, event);
  return [...byId.values()].sort((a, b) => b.timestamp - a.timestamp || (b.id < a.id ? -1 : b.id > a.id ? 1 : 0));
}
// The event of `events` nearest to `seconds`; null when there are none.
export const nearestEvent = (events, seconds) => events.reduce((best, event) =>
  (best == null || Math.abs(event.timestamp - seconds) < Math.abs(best.timestamp - seconds) ? event : best), null);

// Inputs that say what the user did, by type, and how each is counted; the rest
// (releases and pointer moves) are folded away under the ones shown.
const SHOWN = { key_press: ["key", "keys"], mouse_down: ["click", "clicks"], mouse_scroll: ["scroll", "scrolls"] };
const RELEASES = new Set(["key_release", "mouse_up"]);

export const shownInputs = (inputs) => inputs.filter((i) => i.type in SHOWN);

// "3 keys", "1 click", …, then "screenshot" when the event has one.
export function chips(event) {
  const counts = new Map();
  for (const input of shownInputs(event.inputs)) counts.set(input.type, (counts.get(input.type) ?? 0) + 1);
  const words = Object.keys(SHOWN).filter((type) => counts.has(type)).map((type) => {
    const n = counts.get(type);
    return `${n} ${SHOWN[type][n === 1 ? 0 : 1]}`;
  });
  return event.screenshot_url ? [...words, "screenshot"] : words;
}

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

// "4 PM": the hour starting at `seconds`.
export const hourLabel = (seconds) => new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: "numeric" });

export const clock = (seconds, withSeconds = false) => new Date(seconds * 1000).toLocaleTimeString(undefined,
  { hour: "numeric", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}) });

// "41 min", "3 h 46 min".
export function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

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
