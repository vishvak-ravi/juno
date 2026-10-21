// What every page draws the same way: elements, the one pending state of a load,
// paged lists, and the timeline, prediction and status views. Each page adds its
// own shell (how screens are chosen) and chat flow around these.
import { BASE, api, patch } from "./api.js";

export const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};
export const time = (seconds) => (seconds == null ? "" : new Date(seconds * 1000).toLocaleString());
export const status = (text) => el("p", { className: "status", textContent: text });
export const messageOf = (error) => String(error.message || error);
// A URL the server gave, made loadable from this page.
export const url = (path) => (path.startsWith("/") ? BASE + path : path);

// Fills `target` with what `work` resolves to, as one pending state, shown only
// when the wait is long enough to notice. `current()` turning false means the
// user has moved on, and the result is dropped.
export async function load(target, work, current = () => true) {
  const pending = setTimeout(() => current() && target.replaceChildren(status("Loading…")), 300);
  try {
    const nodes = await work();
    if (current()) target.replaceChildren(...nodes);
  } catch (error) {
    if (current()) target.replaceChildren(status(messageOf(error)));
  } finally {
    clearTimeout(pending);
  }
}

// One paged list, with its first page already fetched. `render(item)` makes the
// list element for one item; "Load more" fetches the next page with ?after=<last_id>.
// `first` is the first page when the caller has fetched it already.
export async function paged(path, render, first = null) {
  const list = el("ul");
  const more = el("button", { className: "more", textContent: "Load more", hidden: true });
  const add = (page) => {
    list.append(...page.data.map(render));
    more.hidden = !page.has_more;
    more.onclick = async () => {
      more.disabled = true;
      try {
        add(await api(`${path}?limit=20&after=${page.last_id}`));
      } catch (error) {
        list.append(el("small", { textContent: messageOf(error) }));
      }
      more.disabled = false;
    };
  };
  first ??= await api(`${path}?limit=20`);
  if (!first.data.length) list.append(el("small", { textContent: "None yet." }));
  add(first);
  return el("section", {}, list, more);
}

// Timeline: GET /events, every device's events newest first, read-only. One
// event is its caption, its inputs (one line each) and its screenshot.
const inputLine = ({ type, timestamp, ...details }) =>
  el("li", { className: "event", textContent: `${time(timestamp)}  ${type}  ${JSON.stringify(details)}` });
export const timelineEvent = (e) => el("li", {}, el("article", {},
  el("b", { textContent: `${time(e.timestamp)} · ${e.device_id}` }),
  el("p", { textContent: e.caption ?? "No caption yet." }),
  el("small", { textContent: e.id }),
  el("ul", {}, ...e.inputs.map(inputLine)),
  ...(e.screenshot_url ? [el("img", { alt: "", loading: "lazy", src: url(e.screenshot_url) })] : [])));

// Predictions: one graded prediction (GET /predictions/{id}) in full: the
// prediction beside what actually came next, what the predictor was sent
// (screenshots, then text), and each judge's prompt and reply.
export const score = (value) => (value == null ? "–" : value.toFixed(2));
export const pres = (text) => el("pre", { className: "wrap", textContent: text ?? "" });
const judge = (name, prompt, reply) => (prompt == null ? [] : [
  el("details", {}, el("summary", { textContent: `${name} judge prompt` }), pres(prompt)),
  el("h3", { textContent: `${name} judge reply` }), pres(reply)]);
export function predictorInput(messages) {
  const parts = messages.flatMap((m) => (Array.isArray(m.content) ? m.content : [{ type: "text", text: m.content }]));
  const shots = parts.filter((part) => part.type === "image_url").map((part) => {
    const img = el("img", { alt: "", loading: "lazy", src: url(part.image_url.url) });
    img.onclick = () => img.classList.toggle("big");
    return img;
  });
  return [...(shots.length ? [el("div", { className: "shots" }, ...shots)] : []),
          ...parts.filter((part) => part.type !== "image_url").map((part) => pres(part.text))];
}
// What went wrong in grading one prediction, one line each.
export const problemsOf = (g) => [g.error, ...g.validation_errors,
  ...g.unparsed_replies.map((name) => `The ${name} judge's reply was not the JSON asked for; scored 0.`)]
  .filter(Boolean);
export function predictionDetail(g) {
  const { predictor: p, grader } = g;
  return [
    ...problemsOf(g).map(status),
    el("div", { className: "compare" },
      el("div", {}, el("h3", { textContent: "Predicted" }), pres(g.prediction ?? "No prediction.")),
      el("div", {}, el("h3", { textContent: "Actually next" }), pres(g.ground_truth))),
    el("h3", { textContent: `Predictor input · ${p.model}${p.api_base ? ` at ${p.api_base}` : ""} · max_tokens ${p.max_tokens} · temperature ${p.temperature}` }),
    ...predictorInput(g.messages),
    el("h3", { textContent: `Grader · ${grader.model}${grader.api_base ? ` at ${grader.api_base}` : ""}` }),
    ...judge("Accuracy", g.accuracy_prompt, g.accuracy_reply),
    ...judge("Formatting", g.formatting_prompt, g.formatting_reply),
    el("h3", { textContent: "Sample" }),
    pres(JSON.stringify({ id: g.id, session_id: g.session_id, run: g.run, index: g.index, dataset: g.dataset,
                          cutoff: time(g.cutoff_ts), future_len: g.future_len }, null, 2)),
  ];
}

// Status: GET /control. The server's annotation switch, then one recording switch
// per device. A switch shows what was asked for, the line below it what the queue
// or the device last reported. `onChanged(control)` redraws the page's view after
// a switch is set; `extra` goes between the heading and the device switches.
// The checkbox of one switch: `set(on)` asks the server, `onChanged` gets its
// answer; on failure the box flips back and `line` says why.
export function switchBox(on, line, set, onChanged, props = {}) {
  const box = el("input", { type: "checkbox", checked: on, ...props });
  box.onchange = async () => {
    box.disabled = true;
    try {
      onChanged(await set(box.checked));
    } catch (error) {
      box.checked = !box.checked;
      box.disabled = false;
      line.textContent = messageOf(error);
    }
  };
  return box;
}
export function switchRow(label, on, note, set, onChanged) {
  const line = el("small", { textContent: note });
  const box = switchBox(on, line, set, onChanged);
  return el("li", { className: "switch" }, el("label", {}, box, ` ${label}`), line);
}
export const queueNote = ({ annotate, queue }) => [
  `${queue.queued.live} live · ${queue.queued.requested} requested · ${queue.queued.backfill} backfill`,
  `${queue.running} running`,
  ...(annotate ? [] : ["paused"]),
  ...(queue.paused_seconds > 0 ? [`waiting ${Math.ceil(queue.paused_seconds)} s for the provider`] : [])].join(" · ");
export function deviceNote(d) {
  if (!d.online) return d.reported_at ? `offline · last seen ${time(d.reported_at)}` : "offline";
  const running = d.reported.capture === "running";
  const doing = running ? `recording ${d.reported.session_id}` : "not recording";
  return running === d.capture ? doing : `${doing} · ${d.capture ? "starting" : "stopping"}…`;
}
export function controlView(control, onChanged, extra = []) {
  const devices = Object.entries(control.devices);
  return [
    el("h2", { textContent: "Server" }),
    el("ul", {}, switchRow("Annotation", control.server.annotate, queueNote(control.server),
                           (on) => patch("/control/server", { annotate: on }), onChanged)),
    el("h2", { textContent: "Recording" }),
    ...extra,
    devices.length ? el("ul", {}, ...devices.map(([id, d]) => switchRow(id, d.capture, deviceNote(d),
                                                                       (on) => patch(`/control/devices/${id}`, { capture: on }), onChanged)))
      : el("small", { textContent: "No device has reported yet." })];
}
