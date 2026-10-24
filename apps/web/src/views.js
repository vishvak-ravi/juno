// What every page draws the same way: elements, the one pending state of a load,
// paged lists, and the timeline, proposal and status views. Each page adds its
// own shell (how screens are chosen) and chat flow around these.
import { BASE, api, patch } from "./api.js";
import { clock, dayName, eventLight, eventWords, lightOf, modelName, monthDay, plural, shortDay, stateWords, taskCalls, taskPlace, trailLine } from "./proposals.js";

export const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};
export const time = (seconds) => (seconds == null ? "" : new Date(seconds * 1000).toLocaleString());
export const status = (text) => el("p", { className: "status", textContent: text });
// What is on its way, as shimmering lines in its place.
export const pending = () => el("div", { className: "pending", role: "status", ariaLabel: "Loading" }, el("i"), el("i"), el("i"));
export const messageOf = (error) => String(error.message || error);
// A URL the server gave, made loadable from this page.
export const url = (path) => (path.startsWith("/") ? BASE + path : path);

// Fills `target` with what `work` resolves to, as one pending state, shown only
// when the wait is long enough to notice. `current()` turning false means the
// user has moved on, and the result is dropped.
export async function load(target, work, current = () => true) {
  const wait = setTimeout(() => current() && target.replaceChildren(pending()), 300);
  try {
    const nodes = await work();
    if (current()) target.replaceChildren(...nodes);
  } catch (error) {
    if (current()) target.replaceChildren(status(messageOf(error)));
  } finally {
    clearTimeout(wait);
  }
}

// Makes `nodes` the children of `parent`, in that order, moving only what has
// to move: nodes not there yet are inserted, children not in `nodes` removed,
// the rest left where they are so their state (open, focused, selected) stays.
// Unless motion is reduced, a child that stays slides from where it was to
// where it is and a new one fades in, so what lands above pushes the rest down.
// Returns the nodes inserted.
export function reconcile(parent, nodes) {
  const wanted = new Set(nodes);
  const animate = parent.isConnected && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const before = new Map();
  if (animate) for (const child of parent.children) if (wanted.has(child)) before.set(child, child.getBoundingClientRect().top);
  for (const child of [...parent.children]) if (!wanted.has(child)) child.remove();
  const added = [];
  let at = parent.firstElementChild;
  for (const node of nodes) {
    if (node === at) {
      at = at.nextElementSibling;
      continue;
    }
    if (node.parentNode !== parent) added.push(node);
    parent.insertBefore(node, at);
  }
  if (!animate) return added;
  for (const [node, top] of before) {
    const delta = top - node.getBoundingClientRect().top;
    if (delta) node.animate([{ transform: `translateY(${delta}px)` }, { transform: "none" }], { duration: 300, easing: "ease-out" });
  }
  for (const node of added) node.animate([{ opacity: 0, transform: "translateY(-8px)" }, { opacity: 1, transform: "none" }], { duration: 300, easing: "ease-out" });
  return added;
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

// Proposals: one proposal (GET /proposals/{id}) in full, drawn the same way on
// both pages: the decision bar, the action and why it helps, the task it came
// from, the reframings, the insights it rests on, its trail, then the folds
// (what else was considered, the day it came from, the model calls).
// `decide(event, detail?)` sends the event (PATCH /proposals/{id}) and draws the
// page again; `openTask(task)` and `openSession(sessionId)` are the page's own
// ways on, left out where a page has none.
export const score = (value) => (value == null ? "–" : value.toFixed(2));
export const pres = (text) => el("pre", { className: "wrap", textContent: text ?? "" });
const sec = (text, note) => el("h2", { className: "sec" }, text, ...(note ? [el("small", { textContent: note })] : []));
const callout = (label, text, onClick) => {
  const node = el("p", { className: `parent ${onClick ? "link" : ""}` }, el("b", { textContent: label }), el("span", { textContent: text }));
  if (onClick) node.onclick = onClick;
  return node;
};
const fold = (title, note, body, onOpen) => {
  const details = el("details", { className: "section" },
    el("summary", {}, el("b", { textContent: title }), el("span", { textContent: note })), el("div", { className: "section-body" }, ...body));
  details.ontoggle = () => { if (details.open && onOpen) onOpen(title); };
  return details;
};
// One insight the task was ranked against: its title and tagline, its rank score as a meter, and a word on
// whether the proposer cited it, was given it, or was not given it. The scores of one task sit on one scale,
// the best of them full.
export function insightRow(insight, ranked, note, best = 1) {
  return el("div", { className: `row node ranked ${note === "not given" ? "unused" : ""}` },
    el("span", {}, el("b", { textContent: insight.title }), el("small", { textContent: insight.tagline })),
    el("span", { className: "rank", title: `rank score ${score(ranked.score)}` }, el("span", { className: "track dim" }, el("span", { style: `width: ${Math.round((ranked.score / best) * 100)}%` })),
      el("span", { className: "note", textContent: note })));
}
const bestRank = (ranked) => Math.max(0.01, ...ranked.map((r) => r.score ?? 0));
// Where a task stands against the run's line, in words.
const placeWords = { asked: "asked", "asked by you": "asked by you", "almost asked": "just under the line", "not asked": "under the line", "not scored": "not scored" };
const trailItem = (d) => el("li", { className: d.event === "edited" ? "almost" : "" },
  el("span", { className: "when", textContent: `${monthDay(d.at)}, ${clock(d.at)}` }),
  el("span", { className: `light ${eventLight(d)}` }),
  el("span", {}, el("b", { textContent: eventWords(d) }), ` · ${d.where}`));
// The day's sessions as blocks across its 24 hours.
function ribbon(run) {
  const fraction = (s) => { const d = new Date(s * 1000); return (d.getHours() * 3600 + d.getMinutes() * 60) / 86400; };
  const track = el("span", { className: "track" }, ...run.sessions.map((s) => {
    const block = el("i", { className: "blk", title: `${s.device_id} · ${clock(s.start)} – ${clock(s.end)} · ${plural(s.actions, "action")}` });
    block.style.left = `${fraction(s.start) * 100}%`;
    block.style.width = `${Math.max(0.3, (fraction(s.end) - fraction(s.start)) * 100)}%`;
    return block;
  }));
  return el("div", { className: "dayline" }, el("span", { className: "lbl", textContent: shortDay(run.day) }),
    el("span", {}, track, el("div", { className: "hrs" }, ...["0", "6", "12", "18", "24"].map((h) => el("span", { textContent: h })))));
}
// The run's model calls as rows: step, what for, its share of the longest, seconds and tokens.
export function callRows(calls) {
  const longest = Math.max(1, ...calls.map((c) => c.seconds));
  return el("div", { className: "calls" }, ...calls.flatMap((c) => [
    el("span", { textContent: c.step }),
    el("span", {}, el("span", { className: "track dim" }, el("span", { style: `width: ${(c.seconds / longest) * 100}%` })), el("small", { textContent: ` ${c.what}` })),
    el("span", { className: "num", textContent: `${c.seconds.toFixed(1)} s` }),
    el("span", { className: "num", textContent: c.error ? "failed" : c.prompt_tokens == null ? "" : `${c.prompt_tokens.toLocaleString()} → ${c.completion_tokens}` })]));
}
export function proposalPage(p, { decide, openTask, openSession, where }) {
  const { run, whole_task: task, state } = p;
  const page = el("div", { className: "page" });
  page.append(el("p", { className: "meta", textContent: `Proposal · for ${dayName(p.day)}` }),
    el("h1", { textContent: p.title }));
  const bar = el("div", { className: "decide" });
  bar.append(el("span", { className: "state" }, el("span", { className: `light ${lightOf(state)}` }),
    state.decided ? `${trailLine(state)}${state.decided.detail ? ` · “${state.decided.detail}”` : ""}` : stateWords(state)));
  const body = el("div", { className: "body" }, el("p", { textContent: p.action }));
  let draft = null;
  if (!state.decided) {
    const accept = el("button", { className: "primary", textContent: "Accept" });
    accept.onclick = () => decide("accepted", draft && draft.value !== p.action ? draft.value : undefined);
    const dismiss = el("button", { className: "quiet", textContent: "Dismiss" });
    dismiss.onclick = () => decide("dismissed");
    const later = el("button", { className: "quiet", textContent: "Later" });
    later.onclick = () => decide("snoozed");
    const edit = el("button", { className: "quiet", textContent: state.edited ? "Edited" : "Edit" });
    edit.onclick = () => {
      if (draft) { draft.focus(); return; }
      draft = el("textarea", { className: "draft", value: p.action, ariaLabel: "The action, amended" });
      draft.onchange = () => decide("edited", draft.value);
      body.replaceWith(draft);
      draft.focus();
    };
    bar.append(accept, dismiss, later, edit);
  } else {
    const reopen = el("button", { className: "quiet", textContent: "Reopen" });
    reopen.onclick = () => decide("reopened");
    bar.append(reopen);
  }
  page.append(bar, body, callout("Why it helps", p.benefit),
    callout(`Task · ${placeWords[taskPlace(task, run)]}`, task.task, openTask && (() => openTask(task))));
  if (task.reframings.length) page.append(sec("Reframed as"), el("ul", { className: "evidence" }, ...task.reframings.map((r) => el("li", {}, el("span", { textContent: r })))));
  const byId = new Map(p.insights.map((i) => [i.id, i]));
  const given = task.ranked.filter((r) => r.given && byId.has(r.id));
  const best = bestRank(task.ranked);
  page.append(sec("Rests on", `what the proposer was told about you`),
    ...given.map((r) => insightRow(byId.get(r.id), r, p.insight_ids.includes(r.id) ? "cited" : "given", best)));
  page.append(sec("Trail"), el("ul", { className: "trail-list" }, ...p.trail.map(trailItem)));
  const notGiven = task.ranked.filter((r) => !r.given && byId.has(r.id));
  const expanded = (title) => decide("expanded", title, { quiet: true });
  const sessions = run.sessions.map((s) => {
    const open = el("button", { className: "row event", disabled: !openSession, title: openSession ? "Open this session in the timeline" : "" },
      el("span", { className: "when", textContent: clock(s.start) }),
      el("span", { className: "caption", textContent: `${s.device_id} · ${clock(s.start)} – ${clock(s.end)} · ${plural(s.actions, "action")}` }),
      el("span", { className: "go", textContent: "Timeline" }));
    if (openSession) open.onclick = () => openSession(s.session_id);
    return open;
  });
  page.append(el("div", { className: "sections" },
    fold("Also considered", `${plural(task.considered.length, "action")} cut · ${plural(notGiven.length, "insight")} ranked lower`, [
      ...(task.considered.length ? [el("h3", { textContent: "Cut by the proposer" }),
        el("ul", { className: "evidence" }, ...task.considered.map((c) => el("li", {}, el("span", {}, el("b", { textContent: `${c.title}. ` }), c.why))))] : []),
      ...(notGiven.length ? [el("h3", { textContent: "Ranked lower, not given" }),
        ...notGiven.map((r) => insightRow(byId.get(r.id), r, "not given", best))] : [])], expanded),
    fold("The day", `${run.actions.toLocaleString()} actions in ${plural(run.sessions.length, "session")}, ${shortDay(p.day)}`, [ribbon(run), ...sessions], expanded),
    fold("How it was made", `${plural(p.calls.length, "model call")} · ${modelName(run.models.action)}`,
      [callRows(p.calls), ...taskCalls(p.calls, task).filter((c) => c.step === "actions" && c.prompt).flatMap((c) => [
        el("h3", { textContent: "The proposer's prompt" }), pres(c.prompt), el("h3", { textContent: "Its reply" }), pres(c.reply)])], expanded)));
  void where;
  return page;
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
