// What the desktop browser makes of the routes' rows, without drawing anything:
// events grouped into sessions and their inputs counted, graded predictions
// grouped into runs and their <action> lists read, devices reduced to one state.
// Every value here is computed from rows already fetched; nothing is requested.

// Event `<session id>-<row as six or more digits>` (juno.events.EVENT_ID).
export const sessionOf = (eventId) => eventId.replace(/-\d{6,}$/, "");

// The events of one page grouped by session, in the order the page lists them.
// A session's counts are of the rows loaded, not of the whole session.
export function groupSessions(events) {
  const sessions = new Map();
  for (const event of events) {
    const id = sessionOf(event.id);
    if (!sessions.has(id)) sessions.set(id, { id, device: event.device_id, events: [] });
    sessions.get(id).events.push(event);
  }
  return [...sessions.values()].map((session) => {
    const times = session.events.map((e) => e.timestamp);
    return { ...session, start: Math.min(...times), end: Math.max(...times),
             captioned: session.events.filter((e) => e.caption != null).length };
  });
}

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

// The local day `daysBack` days before `now`, as GET /events' since and until.
export function day(daysBack, now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack);
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack + 1);
  return { since: start.getTime() / 1000, until: end.getTime() / 1000 - 0.001, start };
}
export const dayLabel = (daysBack, start) => (daysBack === 0 ? "Today" : daysBack === 1 ? "Yesterday"
  : start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }));

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
