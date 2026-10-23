// The timeline and predictions panes of the desktop browser. The timeline's
// left pane lists sessions, newest first, each opening to its sequences and
// their actions, with the moment rail down its edge; the detail pane shows a
// calendar of sessions and moments until an action is opened, then that
// action with a replay of its inputs and the frames of its sequence. The
// predictions pane lists the graded runs, a run opening to its predictions, one
// opening in full in the detail pane. A screenshot is requested only when its
// event is opened; a prediction's prompts and the predictor's screenshots only
// when it is opened and their section expanded.
import { api } from "../../../web/src/api.js";
import { el, load, messageOf, pres, predictorInput, problemsOf, score, status, time, url } from "../../../web/src/views.js";
import {
  MOMENT_TYPES, actions, clock, conversationsOf, day, dayBlocks, dayLabel, daysFrom, foldedNote, gridEdges, groupRuns, indexCurve,
  inputLine, liveSessions, matches, modelName, momentText, monthGrid, offset, railLines, sequencesOf, shade, shownInputs,
  weekOf, weekStart, weekdays,
} from "./rows.mjs";
import { replaySteps } from "./activity.mjs";

// The routes' largest page.
const PAGE = 100;
// Rows of an open run shown before its "more" button.
const SHOWN_ROWS = 6;
// How long a moment's divider stays lit after its line on the rail is pressed.
const LIT_MS = 1500;
// The sessions list grows a week at a time.
const WEEK = 7 * 86400;
// Pixels a moment takes on the rail, between the zoom's ends, and a day divider's.
const PITCH = { min: 2, max: 14, start: 8 };
const DIVIDER = 6;
const PITCH_KEY = "juno.timeline.pitch";
// The sequences shown either side of the open action's in the frames strip.
const SEQUENCES_AROUND = 1;
// Milliseconds between the steps of a replay.
const REPLAY_MS = 900;

const ICONS = {
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/>'
    + '<line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  down: '<polyline points="6 9 12 15 18 9"/>',
  left: '<polyline points="15 18 9 12 15 6"/>',
  right: '<polyline points="9 18 15 12 9 6"/>',
  up: '<polyline points="18 15 12 9 6 15"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  minus: '<line x1="5" y1="12" x2="19" y2="12"/>',
  play: '<polygon points="7 4 20 12 7 20 7 4"/>',
};
export function icon(paths, size = 16) {
  const template = document.createElement("template");
  template.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" `
    + `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[paths] ?? paths}</svg>`;
  return template.content.firstChild;
}
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

// The row whose item is open in the detail pane, marked aria-current; found
// again by its data-key when its list is drawn anew.
let current = null;
export function select(button) {
  current?.removeAttribute("aria-current");
  current = button;
  button?.setAttribute("aria-current", "true");
}
const reselect = (root) => {
  const key = current?.dataset.key;
  const again = key && root.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (again) select(again);
};

// The rows of a run: the first SHOWN_ROWS of them and a button for the rest.
function rowList(items, moreLabel) {
  const list = el("ul", { className: "rows" }, ...items);
  if (items.length > SHOWN_ROWS) {
    items.slice(SHOWN_ROWS).forEach((li) => { li.hidden = true; });
    const reveal = el("button", { className: "reveal", textContent: moreLabel(items.length - SHOWN_ROWS) });
    const more = el("li", {}, reveal);
    reveal.onclick = () => { items.forEach((li) => { li.hidden = false; }); more.remove(); };
    list.append(more);
  }
  return list;
}
// One session, sequence or run: a summary that opens to `body`.
function group(key, summary, body) {
  const details = el("details", { className: "group" }, el("summary", {}, ...summary, icon("down")), body);
  details.dataset.key = key;
  return details;
}

// The groups of a paged route: every row fetched so far, grouped by `groupRows`
// and each drawn by `draw`. "Load more" fetches the next page and draws the
// groups again, keeping open the ones that were; the first is open to begin with.
async function groupedPages(path, groupRows, draw, empty) {
  const rows = [];
  const list = el("div", { className: "groups" });
  const more = el("button", { className: "more", textContent: "Load more", hidden: true });
  const add = (page) => {
    const open = new Set([...list.querySelectorAll("details[open]")].map((d) => d.dataset.key));
    const first = !rows.length;
    rows.push(...page.data);
    const groups = groupRows(rows);
    const drawn = groups.map(draw);
    drawn.forEach((d, n) => { d.open = first ? n === 0 : open.has(d.dataset.key); });
    list.replaceChildren(...(drawn.length ? drawn : [status(empty)]));
    reselect(list);
    more.hidden = !page.has_more;
    more.onclick = async () => {
      more.disabled = true;
      try {
        add(await api(`${path}&after=${encodeURIComponent(page.last_id)}`));
      } catch (error) {
        list.append(status(messageOf(error)));
      }
      more.disabled = false;
    };
    return groups;
  };
  const groups = add(await api(path));
  return { nodes: [list, more], groups };
}

// One lazily filled section of a detail page: `build()` runs the first time it is opened.
function section(title, note, build) {
  const body = el("div", { className: "section-body" });
  const details = el("details", { className: "section" },
    el("summary", {}, el("b", { textContent: title }), el("span", {}, note, icon("down"))));
  details.ontoggle = () => {
    if (!details.open || body.parentNode) return;
    body.append(...build());
    details.append(body);
  };
  return details;
}

// A horizontal bar for a score from 0 to 1.
const bar = (value, className = "") => el("span", { className: `track ${className}` },
  el("span", { style: `width: ${Math.round((value ?? 0) * 100)}%` }));
const shortScore = (value) => score(value).replace(/^0\./, ".");

const SVG = "http://www.w3.org/2000/svg";
const svg = (tag, attributes = {}) => {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
};

// Timeline

const INDEX_NAMES = { sensitivity: "Sensitivity", agent: "Assistant in focus", error: "Error on screen", error_shown: "Error on screen",
                      agent_needs_input: "Waiting for your answer" };
const indexName = (name) => INDEX_NAMES[name] ?? name.replaceAll("_", " ");
const momentTone = (type) => `var(--moment-${type}, var(--dim))`;
const pct = (fraction) => `${(Math.min(1, Math.max(0, fraction)) * 100).toFixed(3)}%`;
const sessionPath = (since, until) => `/sessions?since=${since}${until == null ? "" : `&until=${until}`}&order=desc&limit=${PAGE}`;
// Every page of a sessions route, newest first.
async function allPages(path) {
  let page = await api(path);
  const rows = [...page.data];
  while (page.has_more) {
    page = await api(`${path}&after=${encodeURIComponent(page.last_id)}`);
    rows.push(...page.data);
  }
  return rows;
}
function savedPitch() {
  try {
    const saved = Number(localStorage.getItem(PITCH_KEY));
    return saved >= PITCH.min && saved <= PITCH.max ? saved : PITCH.start;
  } catch {
    return PITCH.start;
  }
}
const savePitch = (pitch) => { try { localStorage.setItem(PITCH_KEY, String(pitch)); } catch { /* the zoom starts over next time */ } };

// The keys of the open view: Escape back to the calendar, the arrows stepping
// through the open session's events, while nothing is being typed.
let keys = null;
document.addEventListener("keydown", (event) => {
  if (!keys || event.altKey || event.ctrlKey || event.metaKey || event.target.closest?.("input, textarea")) return;
  const handler = { Escape: keys.escape, ArrowLeft: () => keys.step(-1), ArrowRight: () => keys.step(1) }[event.key];
  if (!handler) return;
  event.preventDefault();
  handler();
});

// The timeline pane and what it opens in the detail pane. `control()` is the
// latest GET /control, for which sessions are being recorded.
export function timelinePane(pane, detail, control) {
  const sessions = new Map();  // every session fetched for the list, by id
  const details = new Map();   // a session's events, by id, once fetched
  const groups = new Map();    // a session's group in the list, by id, kept across redraws
  const hidden = new Set();    // moment types not shown
  let since = weekOf(new Date(), weekStart(navigator.language)).getTime() / 1000;  // the list runs from here to now
  let index = null;            // the index drawn in the calendar's blocks

  // The list of sessions, newest first, a heading where the day changes.
  const list = el("div", { className: "scroll" });
  const groupList = el("div", { className: "groups" });
  const earlier = el("button", { className: "more", textContent: "Earlier" });
  const rail = momentRail(list, (line) => openMoment(line));
  const nowButton = el("button", { className: "new", ariaLabel: "Now" }, icon("up", 14), "Now");
  pane.replaceChildren(el("header", {}, el("h2", { textContent: "Timeline" }), nowButton),
                       el("div", { className: "body" }, rail.strip, list));
  const live = () => liveSessions(control());
  const detailOf = async (id) => {
    if (!details.has(id)) details.set(id, await api(`/sessions/${encodeURIComponent(id)}`));
    return details.get(id);
  };
  const ordered = () => [...sessions.values()].sort((a, b) => b.start - a.start);
  const drawList = () => {
    const nodes = [];
    let lastDay = null;
    for (const session of ordered()) {
      const { since: dayStart, start } = day(new Date(session.start * 1000));
      if (dayStart !== lastDay) nodes.push(el("p", { className: "date", textContent: dayLabel(start) }));
      lastDay = dayStart;
      if (!groups.has(session.id)) groups.set(session.id, sessionGroup(session));
      nodes.push(groups.get(session.id));
    }
    groupList.replaceChildren(...(nodes.length ? nodes : [status("Nothing recorded this week.")]));
    rail.set(railLines(ordered()), hidden);
  };
  const add = (rows) => {
    for (const session of rows) sessions.set(session.id, session);
    drawList();
  };
  // Another week of sessions before the earliest loaded.
  const loadEarlier = async () => {
    const until = since - 0.001;
    since -= WEEK;
    add(await allPages(sessionPath(since, until)));
  };
  earlier.onclick = async () => {
    earlier.disabled = true;
    try {
      await loadEarlier();
    } catch (error) {
      earlier.before(status(messageOf(error)));
    }
    earlier.disabled = false;
  };
  load(list, async () => {
    add(await allPages(sessionPath(since)));
    return [groupList, earlier];
  });

  function sessionGroup(session) {
    const when = el("b");
    const summary = [el("span", { className: "label" }, when, el("small", { textContent: session.device_id }))];
    const body = el("div", { className: "session-body" });
    const node = group(session.id, summary, body);
    node.classList.add("session");
    node.ontoggle = () => {
      when.textContent = `${clock(session.start)} – ${live().has(session.id) ? "now" : clock(session.end)}`;
      if (!node.open || body.dataset.filled) return;
      body.dataset.filled = "true";
      load(body, async () => {
        const whole = await detailOf(session.id);
        return sequencesOf(whole.events).toReversed().map((sequence) => sequenceGroup(sequence, whole));
      }).then(() => { reselect(body); applyHidden(); rail.layout(); });
    };
    node.ontoggle();
    return node;
  }

  // One sequence: its caption, opening to its actions newest first, a moment's
  // divider above the action it starts at.
  function sequenceGroup(sequence, whole) {
    const first = sequence.events[0];
    const summary = [el("span", { className: "when", textContent: clock(first.timestamp) }),
                     el("span", { className: "label" }, el("b", { className: sequence.caption == null ? "none" : "",
                                                                 textContent: sequence.caption ?? "No caption yet" }))];
    const rows = sequence.events.toReversed().flatMap((event) => [
      ...(event.moments ?? []).map((moment) => momentMark(moment, event)),
      el("li", {}, actionRow(event, whole))]);
    const node = group(`${whole.id}:${sequence.index}`, summary, el("ul", { className: "rows" }, ...rows));
    node.classList.add("seq");
    return node;
  }
  function momentMark(moment, event) {
    const mark = el("li", { className: "moment" }, el("span"), el("span", { className: "what", textContent: momentText(moment) }));
    mark.style.setProperty("--c", momentTone(moment.type));
    mark.dataset.type = moment.type;
    mark.dataset.event = event.id;
    return mark;
  }
  function actionRow(event, whole) {
    const button = el("button", { className: "row event" },
      el("span", { className: "when", textContent: clock(event.timestamp, true) }),
      el("span", { className: event.caption == null ? "caption none" : "caption", textContent: event.caption ?? "No caption yet" }));
    button.dataset.key = event.id;
    button.onclick = () => openEvent(whole, event.id);
    return button;
  }
  // Moments of a hidden type leave the list, the rail and the calendar.
  const applyHidden = () => {
    for (const node of document.querySelectorAll("[data-type]")) node.hidden = hidden.has(node.dataset.type);
    rail.set(railLines(ordered()), hidden);
  };

  // Opens a session's group in the list, loading earlier weeks until it is there.
  async function showSession(session) {
    while (!sessions.has(session.id) && since > session.start) await loadEarlier();
    if (!sessions.has(session.id)) add([session]);
    for (const node of groups.values()) node.removeAttribute("aria-current");
    const node = groups.get(session.id);
    node.setAttribute("aria-current", "true");
    node.open = true;
    node.scrollIntoView({ block: "start", behavior: smooth() });
    return detailOf(session.id);
  }
  async function openMoment({ moment, session }) {
    const whole = await showSession(session);
    openEvent(whole, moment.event_id);
    requestAnimationFrame(() => {
      const mark = groupList.querySelector(`.moment[data-event="${CSS.escape(moment.event_id)}"]`);
      if (!mark) return;
      mark.closest("details.seq").open = true;
      mark.scrollIntoView({ block: "center", behavior: smooth() });
      mark.classList.add("lit");
      setTimeout(() => mark.classList.remove("lit"), LIT_MS);
    });
  }

  // The calendar: a week, a day or a month of sessions, in the detail pane.
  const ws = weekStart(navigator.language);
  let mode = "week";
  let anchor = new Date();
  const title = el("span", { className: "here" });
  const back = el("button", { className: "crumb", hidden: true }, icon("left", 14), "Calendar");
  const previous = el("button", { className: "step", ariaLabel: "Earlier" }, icon("left", 14));
  const next = el("button", { className: "step", ariaLabel: "Later" }, icon("right", 14));
  const today = el("button", { className: "step today", textContent: "Today" });
  const modes = el("span", { className: "views", role: "group", ariaLabel: "Calendar view" },
    ...[["day", "Day"], ["week", "Week"], ["month", "Month"]].map(([value, text]) => {
      const button = el("button", { textContent: text });
      button.dataset.mode = value;
      button.onclick = () => { mode = value; showCalendar(); };
      return button;
    }));
  const chips = el("span", { className: "chips", role: "group", ariaLabel: "Moments shown" }, ...MOMENT_TYPES.map((type) => {
    const chip = el("button", { className: "chip", textContent: momentText({ type }), ariaPressed: "true" });
    chip.style.setProperty("--c", momentTone(type));
    chip.onclick = () => {
      if (hidden.has(type)) hidden.delete(type); else hidden.add(type);
      chip.ariaPressed = String(!hidden.has(type));
      applyHidden();
    };
    return chip;
  }));
  const picker = el("select", { ariaLabel: "Index drawn in each session", hidden: true });
  picker.onchange = () => { index = picker.value; showCalendar(); };
  const crumbs = el("div", { className: "crumbs" }, back, title, el("span", { className: "nav" }, previous, next), today, modes,
                    el("span", { className: "right" }, chips, picker));
  const calBody = el("div", { className: "scroll cal" });
  const calendarNodes = [crumbs, calBody];
  let shown = 0;  // which showCalendar() the body is filled by; an older one's result is dropped
  const shift = (by) => {
    const d = anchor;
    anchor = mode === "month" ? new Date(d.getFullYear(), d.getMonth() + by, 1) : new Date(d.getFullYear(), d.getMonth(), d.getDate() + by * (mode === "week" ? 7 : 1));
    showCalendar();
  };
  previous.onclick = () => shift(-1);
  next.onclick = () => shift(1);
  today.onclick = () => { anchor = new Date(); showCalendar(); };
  back.onclick = () => showCalendar();
  nowButton.onclick = () => { anchor = new Date(); list.scrollTo({ top: 0, behavior: smooth() }); showCalendar(); };

  function showCalendar() {
    const mine = ++shown;
    keys = { escape: () => {}, step: () => {} };
    select(null);
    back.hidden = true;
    for (const node of [previous, next, today, modes]) node.hidden = false;
    for (const button of modes.children) button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    if (detail.firstChild !== crumbs) detail.replaceChildren(...calendarNodes);
    const days = mode === "month" ? monthGrid(anchor.getFullYear(), anchor.getMonth(), ws)
      : daysFrom(mode === "week" ? weekOf(anchor, ws) : day(anchor).start, mode === "week" ? 7 : 1);
    const range = { month: { month: "long", year: "numeric" }, week: { month: "short", day: "numeric" }, day: { weekday: "long", month: "long", day: "numeric" } }[mode];
    title.textContent = mode === "week" ? `${days[0].toLocaleDateString(undefined, range)} – ${days[6].toLocaleDateString(undefined, range)}`
      : anchor.toLocaleDateString(undefined, range);
    next.disabled = days[0] >= day(new Date()).start && mode !== "month";
    load(calBody, () => (mode === "month" ? monthView(days) : columnsView(days)), () => shown === mine);
  }

  // A month: each day shaded by how many events it holds, pressed to open its week.
  async function monthView(days) {
    const { counts } = await api(`/events/activity?edges=${gridEdges(days).join(",")}`);
    const max = Math.max(0, ...counts);
    const todayStart = day(new Date()).start;
    const cells = days.map((date, i) => {
      const cell = el("button", { className: ["cell", date.getMonth() === anchor.getMonth() ? "" : "other",
                                              date.getTime() === todayStart.getTime() ? "today" : ""].filter(Boolean).join(" "),
                                  disabled: date > todayStart, ariaLabel: `${date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}, ${plural(counts[i] ?? 0, "event")}` },
        el("b", { textContent: date.getDate() }));
      cell.style.setProperty("--shade", shade(counts[i] ?? 0, max));
      cell.onclick = () => { anchor = date; mode = "week"; showCalendar(); };
      return cell;
    });
    return [el("div", { className: "month" },
      ...weekdays(ws, navigator.language).map((w) => el("span", { className: "weekday", textContent: w })), ...cells)];
  }

  // Days as columns: a session is a block on each day it touches, one index
  // curving down its right side, its moments pinned on its left; a line marks now.
  async function columnsView(days) {
    const since = day(days[0]).since;
    const until = day(days.at(-1)).until;
    const [rows, indices] = await Promise.all([allPages(sessionPath(since, until)), api(`/indices?since=${since}&until=${until}`)]);
    for (const session of rows) if (!sessions.has(session.id) && session.start >= since) sessions.set(session.id, session);
    const names = indices.buckets.some((b) => b.detected) ? indices.names : [];
    picker.hidden = !names.length;
    picker.replaceChildren(...names.map((name) => el("option", { value: name, textContent: indexName(name) })));
    if (!names.includes(index)) index = names.includes("agent") ? "agent" : names[0] ?? null;
    picker.value = index ?? "";
    const curve = index ? indexCurve(indices, index, since, until).flat() : [];
    const todayStart = day(new Date()).since;
    const now = Date.now() / 1000;
    const grid = el("div", { className: "columns" });
    grid.style.setProperty("--days", days.length);
    grid.append(el("span", { className: "corner" }), ...days.map((date) => el("span", { className: `head${day(date).since === todayStart ? " today" : ""}` },
      el("b", { textContent: date.getDate() }), date.toLocaleDateString(undefined, { weekday: "short" }))));
    grid.append(el("span", { className: "hours" }, ...Array.from({ length: 24 }, (_, h) =>
      el("span", { textContent: h ? clock(day(days[0]).since + h * 3600).replace(/:00/, "") : "", style: `top: ${pct(h / 24)}` }))));
    const columns = days.map(() => el("div", { className: "col" }));
    for (const session of rows) {
      for (const block of dayBlocks(session, days)) {
        const { since: dayStart } = day(days[block.day]);
        const from = dayStart + block.from * 86400;
        const to = dayStart + block.to * 86400;
        const button = el("button", { className: "block", style: `top: ${pct(block.from)}; height: ${pct(Math.max(0.004, block.to - block.from))}` },
          el("b", { textContent: session.device_id }),
          el("small", { textContent: `${clock(from)} – ${live().has(session.id) && block.to >= (now - dayStart) / 86400 - 0.001 ? "now" : clock(to)}` }),
          curvePlot(curve.filter((p) => { const t = since + p.x * (until - since); return t >= from && t <= to; }), from, to, since, until));
        button.dataset.key = session.id;
        button.ariaLabel = `${session.device_id}, ${clock(from)} to ${clock(to)}; open its sequences`;
        button.onclick = () => showSession(session);
        columns[block.day].append(button);
      }
      for (const moment of session.moments) {
        const n = days.findIndex((date) => moment.timestamp >= day(date).since && moment.timestamp <= day(date).until);
        if (n < 0) continue;
        const pin = el("button", { className: "pin", style: `top: ${pct((moment.timestamp - day(days[n]).since) / 86400)}`,
                                   ariaLabel: `${momentText(moment)}, ${clock(moment.timestamp)}`, title: `${momentText(moment)} · ${clock(moment.timestamp)}` });
        pin.style.setProperty("--c", momentTone(moment.type));
        pin.dataset.type = moment.type;
        pin.hidden = hidden.has(moment.type);
        pin.onclick = async () => openEvent(await showSession(session), moment.event_id);
        columns[n].append(pin);
      }
    }
    days.forEach((date, n) => {
      if (!columns[n].children.length) columns[n].classList.add("off");
      if (day(date).since === todayStart) columns[n].append(el("span", { className: "now", style: `top: ${pct((now - todayStart) / 86400)}` }));
    });
    grid.append(...columns);
    // Opens on the hour before now when today is shown, else on the morning.
    const at = days.some((date) => day(date).since === todayStart) ? (now - todayStart) / 86400 - 1 / 24 : 8 / 24;
    requestAnimationFrame(() => { calBody.scrollTop = Math.max(0, at) * grid.querySelector(".col").clientHeight; });
    return [grid];
  }
  // The index through one block, as an area from its left edge, in a unit box.
  function curvePlot(points, from, to, since, until) {
    const plot = svg("svg", { class: "curve", viewBox: "0 0 1 1", preserveAspectRatio: "none", "aria-hidden": "true" });
    if (points.length < 2) return plot;
    const at = (p) => ((since + p.x * (until - since)) - from) / (to - from);
    const d = `M 0 ${at(points[0])} ${points.map((p) => `L ${p.y.toFixed(3)} ${at(p).toFixed(4)}`).join(" ")} L 0 ${at(points.at(-1)).toFixed(4)} Z`;
    plot.append(svg("path", { d }));
    return plot;
  }

  // One action in full, in place of the calendar: a ribbon of its day across
  // the top (pressed to go back), the action, its parent sequence's caption,
  // the screenshot with the inputs replayed over it, and the frames of its
  // sequence and the ones around it.
  function openEvent(whole, id) {
    const events = whole.events;
    const at = events.findIndex((e) => e.id === id);
    const event = events[at];
    if (!event) return;
    const sequences = sequencesOf(events);
    const n = sequences.findIndex((s) => s.index === event.sequence);
    keys = { escape: showCalendar, step: (by) => events[at + by] && openEvent(whole, events[at + by].id) };
    back.hidden = false;
    for (const node of [previous, next, today, modes]) node.hidden = true;
    const dayOf = day(new Date(event.timestamp * 1000));
    title.textContent = `${dayLabel(dayOf.start)} · ${clock(event.timestamp, true)} · ${event.device_id}`;
    const steps = replaySteps(event);
    const page = el("div", { className: "page" },
      el("h1", { className: event.caption == null ? "none" : "", textContent: event.caption ?? "No caption yet" }),
      el("p", { className: "parent" }, el("b", { textContent: "Parent sequence" }),
         el("span", { className: event.sequence_caption == null ? "none" : "", textContent: event.sequence_caption ?? "No caption yet" })),
      ...conversationLinks(event),
      event.screenshot_url ? screenshot(event, steps) : status("No screenshot with this event."),
      ...(steps.length ? [replayBar(steps)] : []),
      framesStrip(sequences.slice(Math.max(0, n - SEQUENCES_AROUND), n + SEQUENCES_AROUND + 1), whole, id),
      inputsFold(event));
    const scroller = el("div", { className: "scroll" }, ribbon(dayOf, event), page);
    detail.replaceChildren(crumbs, scroller);
    const row = groupList.querySelector(`[data-key="${CSS.escape(id)}"]`);
    if (row) {
      row.closest("details.seq").open = true;
      select(row);
      row.scrollIntoView({ block: "nearest" });
    }
  }
  // The day of the open action: its sessions and moments on one line, the action marked.
  function ribbon(dayOf, event) {
    const days = [dayOf.start];
    const track = el("div", { className: "track" });
    for (const session of ordered()) {
      for (const block of dayBlocks(session, days)) {
        track.append(el("i", { className: "blk", style: `left: ${pct(block.from)}; width: ${pct(block.to - block.from)}` }));
      }
      for (const moment of session.moments) {
        if (moment.timestamp < dayOf.since || moment.timestamp > dayOf.until || hidden.has(moment.type)) continue;
        const pin = el("i", { className: "pin", style: `left: ${pct((moment.timestamp - dayOf.since) / 86400)}` });
        pin.style.setProperty("--c", momentTone(moment.type));
        track.append(pin);
      }
    }
    const now = Date.now() / 1000;
    if (now >= dayOf.since && now <= dayOf.until) track.append(el("i", { className: "now", style: `left: ${pct((now - dayOf.since) / 86400)}` }));
    track.append(el("i", { className: "at", style: `left: ${pct((event.timestamp - dayOf.since) / 86400)}` }));
    const button = el("button", { className: "ribbon", title: "Back to the calendar", ariaLabel: `Back to the calendar, ${dayLabel(dayOf.start)}` },
      el("span", { className: "lbl", textContent: dayOf.start.toLocaleDateString(undefined, { month: "short", day: "numeric" }) }),
      el("span", {}, track, el("span", { className: "hrs" }, ...[0, 6, 12, 18, 24].map((h) => el("span", { textContent: h })))));
    button.onclick = () => showCalendar();
    return button;
  }
  // The frames: each sequence's actions in a row, a divider between sequences, the open one lit.
  function framesStrip(sequences, whole, id) {
    const strip = el("ul", { className: "frames" }, ...sequences.flatMap((sequence, n) => [
      ...(n ? [el("li", { className: "sep" })] : []),
      ...sequence.events.map((event) => {
        const frame = el("button", { className: "frame", ariaLabel: `${clock(event.timestamp, true)}: ${event.caption ?? "no caption yet"}`,
                                     title: event.caption ?? "" }, el("small", { textContent: clock(event.timestamp, true) }));
        if (event.id === id) frame.setAttribute("aria-current", "true");
        if (event.moments?.some((m) => !hidden.has(m.type))) {
          const mark = el("i");
          mark.style.setProperty("--c", momentTone(event.moments[0].type));
          frame.append(mark);
        }
        frame.onclick = () => openEvent(whole, event.id);
        return el("li", {}, frame);
      })]));
    requestAnimationFrame(() => strip.querySelector("[aria-current]")?.scrollIntoView({ block: "nearest", inline: "center" }));
    return strip;
  }
  // The inputs behind the replay, as a fold: one line each, the folded ones (releases and pointer moves) after.
  function inputsFold(event) {
    const shown = shownInputs(event.inputs);
    const folded = foldedNote(event.inputs);
    return el("details", { className: "folded" },
      el("summary", {}, icon("right", 14), el("b", { textContent: "Inputs" }), el("span", { className: "num", textContent: plural(event.inputs.length, "input") })),
      shown.length ? inputGrid(shown, event) : status("No keys, clicks or scrolls in this event."),
      ...(folded ? [el("p", { className: "meta", textContent: folded }), inputGrid(event.inputs.filter((input) => !shown.includes(input)), event)] : []));
  }

  showCalendar();
}

// The moment rail: one line per moment of the sessions listed, in the list's
// order, a divider per day, down the left edge of `scroll`. The zoom sets how
// many pixels a moment takes; past the pane's height the rail scrolls with the
// list. A line reads on hover; pressing it calls `open(line)`. The band marks
// the lines whose sessions are in view.
function momentRail(scroll, open) {
  const body = el("div", { className: "rail-body" });
  const band = el("span", { className: "band", hidden: true });
  const zoomIn = el("button", { ariaLabel: "More room per moment" }, icon("plus", 12));
  const zoomOut = el("button", { ariaLabel: "Less room per moment" }, icon("minus", 12));
  const strip = el("nav", { className: "moments", ariaLabel: "Moments" }, el("span", { className: "zoom" }, zoomIn, zoomOut), body);
  let lines = [];
  let nodes = [];
  let pitch = savedPitch();
  const layout = () => {
    const height = body.clientHeight;
    const sizes = nodes.map((node, n) => (node.hidden ? 0 : lines[n].divider ? DIVIDER : pitch));
    const total = sizes.reduce((a, b) => a + b, 0);
    const room = Math.max(1, scroll.scrollHeight - scroll.clientHeight);
    const shift = Math.max(0, total - height) * Math.min(1, scroll.scrollTop / room);
    let y = 0;
    const tops = sizes.map((size) => { const top = y; y += size; return top; });
    nodes.forEach((node, n) => {
      node.style.top = `${tops[n] - shift}px`;
      node.style.height = `${sizes[n]}px`;
    });
    const view = scroll.getBoundingClientRect();
    const inView = lines.map((line, n) => {
      if (line.divider || nodes[n].hidden) return false;
      const box = line.group?.()?.getBoundingClientRect();
      return box && box.bottom > view.top && box.top < view.bottom;
    });
    const first = inView.indexOf(true);
    band.hidden = first < 0;
    if (first >= 0) {
      const last = inView.lastIndexOf(true);
      band.style.top = `${tops[first] - shift}px`;
      band.style.height = `${tops[last] + sizes[last] - tops[first]}px`;
    }
    zoomIn.disabled = pitch >= PITCH.max;
    zoomOut.disabled = pitch <= PITCH.min;
  };
  const zoom = (by) => {
    pitch = Math.max(PITCH.min, Math.min(PITCH.max, pitch + by));
    savePitch(pitch);
    layout();
  };
  zoomIn.onclick = () => zoom(2);
  zoomOut.onclick = () => zoom(-2);
  strip.addEventListener("wheel", (event) => {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      zoom(event.deltaY < 0 ? 1 : -1);
    } else scroll.scrollTop += event.deltaY;
  }, { passive: false });
  let queued = false;
  scroll.addEventListener("scroll", () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; layout(); });
  });
  new ResizeObserver(layout).observe(body);
  return {
    strip,
    layout,
    set(items, hidden) {
      lines = items;
      nodes = items.map((line) => {
        if (line.divider) return el("span", { className: "d" });
        const { moment, session } = line;
        const when = day(new Date(moment.timestamp * 1000)).start;
        line.group = () => scroll.querySelector(`details[data-key="${CSS.escape(session.id)}"] > summary`);
        const node = el("button", { className: "m", hidden: hidden.has(moment.type), ariaLabel: `${momentText(moment)}, ${dayLabel(when)} ${clock(moment.timestamp)}` },
          el("i"), el("span", { className: "tip" }, el("b", { textContent: momentText(moment) }),
                                                   el("small", { textContent: `${dayLabel(when)} · ${clock(moment.timestamp)}` })));
        node.style.setProperty("--c", momentTone(moment.type));
        node.onclick = () => open(line);
        return node;
      });
      body.replaceChildren(band, ...nodes);
      layout();
    },
  };
}

// The screenshot in its reserved box, with the replay's overlays over it: the
// box alone while the image is on its way (a shimmer once that takes long
// enough to notice), then the image whole. A click or scroll is drawn where it
// happened, in the screenshot's own pixels; keys as a bubble.
function screenshot(event, steps) {
  const img = el("img", { className: "shot", alt: `Screenshot at ${clock(event.timestamp, true)}`, src: url(event.screenshot_url) });
  const overlays = steps.map((step, n) => {
    const node = el("span", { className: `ov ${step.kind}`, hidden: true });
    node.dataset.step = n;
    if (step.x != null) {
      node.style.setProperty("--x", step.x);
      node.style.setProperty("--y", step.y);
    } else node.classList.add("bubble");
    const words = step.words;
    node.append(el("b", { textContent: [words.what, ...(words.keys ?? [])].join(" ") }),
                ...(words.text != null ? [el("span", { textContent: words.text })] : []),
                ...(words.detail ? [el("small", { textContent: words.detail })] : []));
    return node;
  });
  const box = el("div", { className: "shot-box" }, img, ...overlays);
  img.onload = () => {
    box.style.setProperty("--w", img.naturalWidth);
    box.style.setProperty("--h", img.naturalHeight);
    box.classList.add("loaded");
  };
  img.onerror = () => box.replaceWith(status("The screenshot could not be loaded."));
  return box;
}
// The bar under the screenshot: a stop per step at its offset, pressed to show
// that step's overlay; play runs through them.
function replayBar(steps) {
  const span = Math.max(1, steps.at(-1).offset);
  const readout = el("span", { className: "num" });
  const stops = steps.map((step, n) => {
    const stop = el("button", { className: "stop", style: `left: ${pct(step.offset / span)}`, ariaLabel: `${offsetText(step.offset)}: ${step.words.what}` });
    stop.onclick = () => show(n);
    return stop;
  });
  const play = el("button", { className: "play", ariaLabel: "Replay the inputs" }, icon("play", 12));
  const bar = el("div", { className: "replay" }, play, el("span", { className: "line" }, ...stops), readout);
  let playing = null;
  const show = (n) => {
    stops.forEach((stop, i) => stop.setAttribute("aria-current", String(i === n)));
    for (const node of bar.parentNode?.querySelectorAll(".ov") ?? []) node.hidden = Number(node.dataset.step) !== n;
    readout.textContent = offsetText(steps[n].offset);
  };
  play.onclick = () => {
    if (playing) {
      clearInterval(playing);
      playing = null;
      return;
    }
    let n = 0;
    show(0);
    playing = setInterval(() => {
      n += 1;
      if (n >= steps.length) {
        clearInterval(playing);
        playing = null;
        return;
      }
      show(n);
    }, REPLAY_MS);
  };
  requestAnimationFrame(() => show(0));
  return bar;
}
const offsetText = (seconds) => `+${seconds.toFixed(2)} s`;

const inputGrid = (inputs, event) => el("div", { className: "inputs" }, ...inputs.flatMap((input) => {
  const [what, details] = inputLine(input);
  return [el("span", { className: "dim", textContent: offset(input, event) }), el("b", { textContent: what }),
          el("span", { textContent: details })];
}));

// This computer's device id, known shortly after startup: a conversation is
// reopened only on the computer that recorded its event, and only from the
// desktop app (window.juno), which can open a terminal.
let here = null;
window.juno?.deviceId().then((id) => { here = id; });

// A button per conversation the event's moments carry, opening it again in a
// terminal; on another computer's event, where it is instead.
function conversationLinks(event) {
  const conversations = conversationsOf(event);
  if (!conversations.length || !window.juno) return [];
  if (event.device_id !== here) return [status(`The Claude Code conversation of this prompt is on ${event.device_id}.`)];
  return [el("div", { className: "resume" }, ...conversations.map((conversation) => {
    const button = el("button", { className: "primary", title: conversation.cwd }, "Resume in Claude Code");
    const said = el("span", { className: "dim" });
    button.onclick = async () => {
      button.disabled = true;
      const { error } = await window.juno.resume(event.device_id, conversation);
      said.textContent = error ?? "";
      button.disabled = false;
    };
    return el("span", {}, button, said);
  }))];
}

// Predictions

const runLabel = (run) => (run.time ? run.time.toLocaleString(undefined,
  { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : run.run);

// The predictions pane: the graded runs, most recently graded first.
export function predictionsPane(pane, detail) {
  keys = null;
  const count = el("small");
  const list = el("div", { className: "scroll" });
  pane.replaceChildren(el("header", {}, el("h2", { textContent: "Predictions" }), count), list);
  load(list, async () => {
    const { nodes, groups } = await groupedPages(`/predictions?limit=${PAGE}`, groupRuns,
      (run) => runGroup(run, detail), "No graded predictions yet.");
    count.textContent = groups.length ? plural(groups.length, "run") : "";
    return nodes;
  });
}

function runGroup(run, detail) {
  const spark = el("span", { className: "spark", role: "img", ariaLabel: `Rewards of ${plural(run.rows.length, "prediction")}` },
    ...run.rows.map((g) => el("span", { className: g.error ? "failed" : "",
                                       style: `height: ${g.error ? 3 : Math.max(2, Math.round((g.reward ?? 0) * 14))}px` })));
  const stats = [plural(run.rows.length, "prediction"), `mean reward ${score(run.mean)}`,
                 ...(run.failed ? [`${run.failed} failed`] : [])].join(" · ");
  const summary = [el("span", { className: "label" },
    el("b", { textContent: runLabel(run) }),
    el("small", { textContent: `${run.predictor}, judged by ${run.grader}` }),
    el("span", { className: "spark-line" }, spark, el("small", { textContent: stats })))];
  const rows = run.rows.map((g) => {
    const first = actions(g.prediction)[0];
    const button = el("button", { className: "row prediction" },
      el("span", { className: "when", textContent: g.index }),
      el("span", { className: first ? "caption" : "caption none", textContent: first ?? "No prediction" }),
      g.error
        ? el("span", { className: "reward" }, el("span", { className: "light changing" }), el("small", { textContent: "failed" }))
        : el("span", { className: "reward" }, bar(g.reward), el("span", { className: "num", textContent: shortScore(g.reward) })));
    button.dataset.key = g.id;
    button.onclick = () => {
      select(button);
      load(detail, async () => [el("div", { className: "scroll" }, predictionPage(await api(`/predictions/${g.id}`), run))],
        () => current === button);
    };
    return el("li", {}, button);
  });
  return group(run.run, summary, rowList(rows, (more) => `${more} more`));
}

// An ordered action list beside the other one: a green light where both hold
// the same action at that position, amber where they differ.
function actionList(title, list, same, raw, className) {
  return el("div", {},
    el("h2", { textContent: title }),
    list.length
      ? el("ol", { className: `actions ${className}` }, ...list.map((action, n) =>
        el("li", {}, el("span", { className: "dim", textContent: n + 1 }),
           el("span", { className: `light ${same[n] ? "recording" : "changing"}` }), el("span", { textContent: action }))))
      : pres(raw ?? "No prediction."));
}

const judge = (prompt, reply) => [el("h3", { textContent: "Prompt" }), pres(prompt), el("h3", { textContent: "Reply" }), pres(reply)];

function predictionPage(g, run) {
  const { predictor: p, grader } = g;
  const predicted = actions(g.prediction);
  const actual = actions(g.ground_truth);
  const shots = g.messages.flatMap((m) => (Array.isArray(m.content) ? m.content : []))
    .filter((part) => part.type === "image_url").length;
  const scores = [["Reward", g.reward, ""], ["Accuracy", g.accuracy, "dim"], ["Formatting", g.formatting, "dim"]];
  return el("div", { className: "page" },
    el("p", { className: "meta", textContent: `Run ${runLabel(run)} · prediction ${g.index} · ${g.device_id} · cut off at ${time(g.cutoff_ts)}` }),
    el("h1", { textContent: `Next ${plural(g.future_len, "action")}` }),
    ...problemsOf(g).map(status),
    el("div", { className: "scores" }, ...scores.flatMap(([name, value, className]) => [
      el("span", { className, textContent: name }), bar(value, className),
      el("span", { className: `num ${className}`, textContent: score(value) })])),
    el("div", { className: "compare" },
      actionList("Predicted", predicted, matches(predicted, actual), g.prediction, ""),
      actionList("Actually next", actual, matches(actual, predicted), g.ground_truth, "truth")),
    el("div", { className: "sections" },
      section("What the predictor saw",
        `${shots ? `${plural(shots, "screenshot")} and the prompt` : "the prompt"} · ${modelName(p.model)} · ${p.max_tokens} tokens`,
        () => [el("p", { className: "meta", textContent: `${p.model}${p.api_base ? ` at ${p.api_base}` : ""} · temperature ${p.temperature}` }),
               ...predictorInput(g.messages)]),
      ...(g.accuracy_prompt == null ? [] : [section("Accuracy judge", `${modelName(grader.model)} · ${score(g.accuracy)}`,
        () => judge(g.accuracy_prompt, g.accuracy_reply))]),
      ...(g.formatting_prompt == null ? [] : [section("Formatting judge", `${modelName(grader.model)} · ${score(g.formatting)}`,
        () => judge(g.formatting_prompt, g.formatting_reply))]),
      section("Sample", `${g.dataset} · future ${g.future_len}`,
        () => [pres(JSON.stringify({ id: g.id, session_id: g.session_id, run: g.run, index: g.index, dataset: g.dataset,
                                     cutoff: time(g.cutoff_ts), future_len: g.future_len }, null, 2))])));
}
