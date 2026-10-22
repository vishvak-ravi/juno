// The timeline and predictions panes of the desktop browser, coarse to fine: the
// left pane lists one day's activity, newest first (what the user typed, pressed,
// clicked and scrolled, read from the events' inputs), or its events on one line,
// or that day's sessions, each fetched whole when opened (or the graded runs, a run opening to
// its predictions), and one event or prediction opens in full in the detail pane.
// A screenshot is requested only when its event is opened; a prediction's prompts
// and the predictor's screenshots only when it is opened and their section expanded.
import { api } from "../../../web/src/api.js";
import { el, load, messageOf, pres, predictorInput, problemsOf, score, status, time, url } from "../../../web/src/views.js";
import {
  actions, chips, clock, day, dayLabel, duration, foldedNote, gridEdges, groupRuns, hourLabel, indexLanes,
  inputLine, liveSessions, matches, modelName, momentText, monthGrid, offset, railItems, shade, shownInputs,
  weekStart, weekdays,
} from "./rows.mjs";
import { activityRail, describe } from "./activity.mjs";

// The routes' largest page.
const PAGE = 100;
// The colours a device's events are drawn in (--tone-0 … in index.html).
const TONES = 6;
// Rows of an open session or run shown before its "more" button.
const SHOWN_ROWS = 6;

const ICONS = {
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/>'
    + '<line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  down: '<polyline points="6 9 12 15 18 9"/>',
  left: '<polyline points="15 18 9 12 15 6"/>',
  right: '<polyline points="9 18 15 12 9 6"/>',
};
export function icon(paths, size = 16) {
  const template = document.createElement("template");
  template.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" `
    + `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[paths] ?? paths}</svg>`;
  return template.content.firstChild;
}
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

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

// The rows of a session or run: the first SHOWN_ROWS of them and a button for the rest.
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
// One session or run: a summary that opens to `body`.
function group(key, summary, body) {
  const details = el("details", { className: "group" }, el("summary", {}, ...summary, icon("down")), body);
  details.dataset.key = key;
  return details;
}
// A row of a group, made visible: its group opened and its hidden siblings shown.
function reveal(button) {
  const details = button.closest("details");
  if (details) details.open = true;
  if (button.closest("li").hidden) details?.querySelector(".reveal")?.click();
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

// Timeline

// The views of the timeline pane, the one shown remembered on this computer.
const VIEWS = [["activity", "Activity"], ["linear", "Linear"], ["sessions", "Sessions"], ["indices", "Indices"]];
const VIEW_KEY = "juno.timeline.view";
function savedView() {
  try {
    const saved = localStorage.getItem(VIEW_KEY);
    return VIEWS.some(([value]) => value === saved) ? saved : "activity";
  } catch {
    return "activity";
  }
}
function saveView(view) {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    // Not remembered; the activity view shows next time.
  }
}

// The calendar under the day button: one month, each day shaded by how many
// events it holds (GET /events/activity for the grid's midnights), days after
// today not pickable. `onPick(date)` is called with the day chosen.
function calendar(onPick) {
  const start = weekStart(navigator.language);
  const title = el("b");
  const previous = el("button", { className: "step", ariaLabel: "Previous month" }, icon("left", 14));
  const next = el("button", { className: "step", ariaLabel: "Next month" }, icon("right", 14));
  const grid = el("div", { className: "grid" });
  const box = el("div", { className: "calendar", hidden: true },
    el("header", {}, previous, title, next),
    el("div", { className: "weekdays" }, ...weekdays(start, navigator.language).map((w) => el("span", { textContent: w }))),
    grid);
  let picked = null;  // the day the pane shows
  let month = null;   // the first of the month drawn
  const same = (a, b) => a.getTime() === b.getTime();
  const draw = (first) => {
    month = first;
    const today = day(new Date()).start;
    title.textContent = first.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    next.disabled = first >= new Date(today.getFullYear(), today.getMonth(), 1);
    const days = monthGrid(first.getFullYear(), first.getMonth(), start);
    load(grid, async () => {
      const { counts } = await api(`/events/activity?edges=${gridEdges(days).join(",")}`);
      const max = Math.max(0, ...counts);
      return days.map((date, i) => {
        const count = counts[i] ?? 0;
        const cell = el("button", {
          textContent: date.getDate(), disabled: date > today,
          className: [date.getMonth() === first.getMonth() ? "" : "other", same(date, today) ? "today" : "", same(date, picked) ? "picked" : ""]
            .filter(Boolean).join(" "),
          ariaLabel: `${date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}, ${plural(count, "event")}`,
        });
        cell.style.setProperty("--shade", shade(count, max));
        cell.onclick = () => onPick(date);
        return cell;
      });
    }, () => month === first);
  };
  previous.onclick = () => draw(new Date(month.getFullYear(), month.getMonth() - 1, 1));
  next.onclick = () => draw(new Date(month.getFullYear(), month.getMonth() + 1, 1));
  return {
    box,
    open(date) {
      picked = day(date).start;
      box.hidden = false;
      draw(new Date(date.getFullYear(), date.getMonth(), 1));
    },
    close() { box.hidden = true; },
  };
}
// An open calendar closes on a click elsewhere or Escape.
const openCalendar = () => document.querySelector(".calendar:not([hidden])");
document.addEventListener("pointerdown", (event) => {
  const open = openCalendar();
  if (open && !event.target.closest?.(".day")) open.hidden = true;
});
document.addEventListener("keydown", (event) => {
  const open = openCalendar();
  if (event.key !== "Escape" || !open) return;
  open.hidden = true;
  open.closest(".day").querySelector("button").focus();
});

// The timeline pane: the view switch, the day shown with a button that opens the
// calendar to pick another, then that day in the view picked. `control()` is the
// latest GET /control, for which sessions are being recorded and which devices
// there are.
export function timelinePane(pane, detail, control) {
  let view = savedView();
  let date = new Date();
  let shown = 0;  // which show() the list is filled by; an older one's result is dropped
  const label = el("span");
  const pick = el("button", { ariaLabel: "Pick a day", ariaExpanded: "false" }, icon("calendar", 14), label);
  const switches = VIEWS.map(([value, text]) => {
    const button = el("button", { textContent: text });
    button.onclick = () => {
      if (view === value) return;
      view = value;
      saveView(view);
      show();
    };
    button.dataset.view = value;
    return button;
  });
  const list = el("div", { className: "scroll" });
  const cal = calendar((picked) => {
    cal.close();
    date = picked;
    show();
  });
  pane.replaceChildren(el("header", {},
    el("span", { className: "views", role: "group", ariaLabel: "Timeline view" }, ...switches),
    el("span", { className: "day" }, pick, cal.box)), list);
  const tone = tones(Object.keys(control()?.devices ?? {}));
  const show = () => {
    const mine = ++shown;
    const { since, until, start } = day(date);
    label.textContent = dayLabel(start);
    switches.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
    const views = {
      activity: () => activityView(since, until, detail),
      linear: () => linearView(since, until, detail, tone),
      sessions: () => sessionsView(since, until, detail, liveSessions(control()), tone),
      indices: () => indicesView(since, until),
    };
    load(list, views[view], () => shown === mine);
  };
  pick.onclick = () => (cal.box.hidden ? cal.open(date) : cal.close());
  new MutationObserver(() => { pick.ariaExpanded = String(!cal.box.hidden); }).observe(cal.box, { attributeFilter: ["hidden"] });
  show();
}

// A device's colour, the same in both views: the devices known to GET /control
// first, in id order, then the others as they are drawn.
function tones(known) {
  const index = new Map([...known].sort().map((id, n) => [id, n]));
  return (id) => {
    if (!index.has(id)) index.set(id, index.size);
    return `var(--tone-${index.get(id) % TONES})`;
  };
}

// A paged list that fetches the next page when its button scrolls into view, or is pressed.
function pager(path, add, label) {
  const more = el("button", { className: "more", textContent: label, hidden: true });
  const next = (page) => {
    add(page);
    more.hidden = !page.has_more;
    more.onclick = async () => {
      more.disabled = true;
      try {
        next(await api(`${path}&after=${encodeURIComponent(page.last_id)}`));
      } catch (error) {
        more.before(status(messageOf(error)));
      }
      more.disabled = false;
    };
  };
  new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting && !more.hidden && !more.disabled) more.click();
  }).observe(more);
  return { more, next };
}

// The activity view: what the user did through the day, newest first, read from
// the inputs of the events fetched (activity.mjs) and drawn anew as each earlier
// page arrives, since an item can go on into the page before. A row opens the
// event its item began in; the step bar steps through the events fetched.
async function activityView(since, until, detail) {
  const path = `/events?since=${since}&until=${until}&order=desc&limit=${PAGE}`;
  const rows = [];  // newest first
  const rail = el("ol", { className: "rail activity" });
  const sequence = { events: () => rows.toReversed(), note: () => null,
                     row: (id) => rail.querySelector(`[data-events~="${CSS.escape(id)}"]`) };
  const { more, next } = pager(path, (page) => {
    rows.push(...page.data);
    const drawn = activityRail(rows).map((entry) =>
      (entry.hour != null ? hourMark(entry.hour) : entry.moment ? momentMark(entry) : activityRow(entry, sequence, detail)));
    rail.replaceChildren(...drawn);
    reselect(rail);
  }, "Earlier activity");
  const first = await api(path);
  if (!first.data.length) return [status("Nothing recorded this day.")];
  next(first);
  return [rail, more];
}

// One item: its time, a dot in its kind's colour, what was done and with what.
function activityRow({ item, named }, sequence, detail) {
  const words = describe(item);
  const button = el("button", { className: `row tick act ${item.kind}` },
    el("span", { className: "when", textContent: clock(item.start, true) }),
    el("span", { className: "node" }),
    el("span", { className: "body" },
      ...(named ? [el("span", { className: "device", textContent: item.device_id })] : []),
      el("span", { className: "line" },
        el("span", { className: "what", textContent: words.what }),
        ...(words.keys ? [el("span", { className: "keys" }, ...words.keys.map((key) => el("kbd", { textContent: key })))] : []),
        ...(words.detail ? [el("span", { className: "detail", textContent: words.detail })] : [])),
      ...(words.text == null ? [] : [el("span", { className: "typed" }, el("span", { textContent: words.text }),
        ...(words.submitted ? [el("span", { className: "enter", textContent: "⏎", title: "Ended with Enter" })] : []))])));
  button.dataset.key = `${item.device_id}:${item.start}`;
  button.dataset.events = item.events.join(" ");
  button.onclick = () => {
    showEvent(sequence, item.events[0], detail);
    select(button);
  };
  return el("li", {}, button);
}

// The linear view: every event of the day on one line, newest first, an hour
// mark where the hour changes and the device named where it changes. Opened
// events step through the rows fetched, earlier and later.
async function linearView(since, until, detail, tone) {
  const path = `/events?since=${since}&until=${until}&order=desc&limit=${PAGE}`;
  const rows = [];  // newest first
  const sequence = { events: () => rows.toReversed(), note: () => null };
  const rail = el("ol", { className: "rail" });
  const { more, next } = pager(path, (page) => {
    rail.append(...railItems(page.data, rows.at(-1)).map((item) =>
      (item.hour != null ? hourMark(item.hour)
        : item.moment ? momentMark(item) : railRow(item, sequence, detail, tone))));
    rows.push(...page.data);
    reselect(rail);
  }, "Earlier events");
  const first = await api(path);
  if (!first.data.length) return [status("Nothing recorded this day.")];
  next(first);
  return [rail, more];
}

const hourMark = (hour) => el("li", { className: "hour" },
  el("span", { className: "when", textContent: hourLabel(hour) }), el("span", { className: "node" }));

// A divider where a moment starts, above the event it starts at.
const momentMark = ({ moment }) => el("li", { className: "moment" },
  el("span"), el("span", { className: "node" }), el("span", { className: "what", textContent: momentText(moment) }));

function railRow({ event, named }, sequence, detail, tone) {
  const button = el("button", { className: "row tick" },
    el("span", { className: "when", textContent: clock(event.timestamp, true) }),
    el("span", { className: "node" }),
    el("span", { className: "body" },
      ...(named ? [el("span", { className: "device", textContent: event.device_id })] : []),
      ...eventLines(event)));
  button.style.setProperty("--tone", tone(event.device_id));
  button.dataset.key = event.id;
  button.onclick = () => showEvent(sequence, event.id, detail);
  return el("li", {}, button);
}

// An event's caption and its chips, as a row shows them.
const eventLines = (event) => [
  el("span", { className: event.caption == null ? "caption none" : "caption", textContent: event.caption ?? "No caption yet" }),
  el("span", { className: "chips" }, ...chips(event).map((chip) => el("span", { className: "chip", textContent: chip })))];

// The indices view: each index of the day (GET /indices) in its own lane from 0
// to 1, a line through the bucket means with a gap where nothing was answered;
// the pointer over the lanes reads every index at that time.
const INDEX_NAMES = { sensitivity: "Sensitivity", agent: "Assistant in focus", error: "Error on screen" };
const indexName = (name) => INDEX_NAMES[name] ?? name.replaceAll("_", " ");
const SVG = "http://www.w3.org/2000/svg";
const svg = (tag, attributes = {}) => {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
};
// Lanes are drawn in a WIDTH × HEIGHT box stretched to the pane.
const WIDTH = 1000;
const HEIGHT = 48;

async function indicesView(since, until) {
  const indices = await api(`/indices?since=${since}&until=${until}`);
  if (!indices.buckets.some((bucket) => bucket.detected)) return [status("No indices for this day yet.")];
  const lanes = indexLanes(indices, since, until);
  // A grid line every 3 hours, a label every 6.
  const hours = Array.from({ length: 7 }, (_, n) => since + (n + 1) * 3 * 3600);
  const drawn = lanes.map(({ name, runs }) => {
    const readout = el("span", { className: "num" });
    const plot = svg("svg", { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, preserveAspectRatio: "none",
                              role: "img", "aria-label": `${indexName(name)} through the day` });
    for (const hour of hours) {
      const x = ((hour - since) / (until - since)) * WIDTH;
      plot.append(svg("line", { class: "grid", x1: x, x2: x, y1: 0, y2: HEIGHT }));
    }
    plot.append(svg("line", { class: "grid", x1: 0, x2: WIDTH, y1: HEIGHT, y2: HEIGHT }));
    for (const run of runs) {
      const points = run.map((p) => `${p.x * WIDTH},${(1 - p.y) * HEIGHT}`).join(" ");
      plot.append(run.length > 1 ? svg("polyline", { class: "series", points })
        : svg("circle", { class: "dot", cx: run[0].x * WIDTH, cy: (1 - run[0].y) * HEIGHT, r: 2 }));
    }
    const cross = svg("line", { class: "cross", y1: 0, y2: HEIGHT, visibility: "hidden" });
    plot.append(cross);
    const points = runs.flat();
    const lane = el("div", { className: "lane" },
      el("div", { className: "lane-head" }, el("b", { textContent: indexName(name) }), readout), plot);
    const read = (fraction) => {
      if (fraction == null) {
        cross.setAttribute("visibility", "hidden");
        readout.textContent = "";
        return;
      }
      cross.setAttribute("x1", fraction * WIDTH);
      cross.setAttribute("x2", fraction * WIDTH);
      cross.setAttribute("visibility", "visible");
      const step = indices.bucket_seconds / (until - since);
      const near = points.find((p) => Math.abs(p.x - fraction) <= step / 2);
      readout.textContent = near ? `${clock(near.start)} · ${score(near.value)}` : `${clock(since + fraction * (until - since))} · –`;
    };
    return { lane, read };
  });
  const box = el("div", { className: "lanes" }, ...drawn.map((d) => d.lane),
    el("div", { className: "axis" }, ...hours.filter((_, n) => n % 2).map((hour) =>
      el("span", { textContent: hourLabel(hour), style: `left: ${((hour - since) / (until - since)) * 100}%` }))));
  box.onpointermove = (event) => {
    const plot = box.querySelector("svg").getBoundingClientRect();
    const fraction = (event.clientX - plot.left) / plot.width;
    drawn.forEach((d) => d.read(fraction >= 0 && fraction <= 1 ? fraction : null));
  };
  box.onpointerleave = () => drawn.forEach((d) => d.read(null));
  return [box];
}

// The sessions view: the day's sessions (GET /sessions), newest first; a session's
// events (GET /sessions/{id}) are fetched the first time it is opened.
async function sessionsView(since, until, detail, live, tone) {
  const path = `/sessions?since=${since}&until=${until}&limit=${PAGE}`;
  const list = el("div", { className: "groups" });
  const { more, next } = pager(path, (page) => {
    list.append(...page.data.map((session) => sessionGroup(session, live, detail, tone)));
    reselect(list);
  }, "Earlier sessions");
  const first = await api(path);
  if (!first.data.length) return [status("Nothing recorded this day.")];
  next(first);
  return [list, more];
}

function sessionGroup(session, live, detail, tone) {
  const summary = [
    el("span", { className: `light ${live.has(session.id) ? "recording" : "idle"}` }),
    el("span", { className: "label" },
      el("b", { textContent: `${session.device_id} · ${clock(session.start)} – ${clock(session.end)}` }),
      el("small", { textContent: [duration(session.end - session.start), plural(session.event_count, "event"),
                                  session.captioned ? `${session.captioned} captioned` : "no captions yet"].join(" · ") })),
  ];
  const body = el("div", { className: "session-body" });
  const details = group(session.id, summary, body);
  details.classList.add("session");
  details.style.setProperty("--tone", tone(session.device_id));
  details.ontoggle = () => {
    if (!details.open || body.dataset.filled) return;
    body.dataset.filled = "true";
    load(body, async () => {
      const whole = await api(`/sessions/${encodeURIComponent(session.id)}`);
      const sequence = { events: () => whole.events, note: () => `session from ${clock(whole.start)}` };
      const rows = whole.events.map((event) => {
        const button = el("button", { className: "row event" },
          el("span", { className: "when", textContent: clock(event.timestamp, true) }), el("span", {}, ...eventLines(event)));
        button.dataset.key = event.id;
        button.onclick = () => showEvent(sequence, event.id, detail);
        return el("li", {}, button);
      });
      const shown = [rowList(rows, (n) => `${n} more until ${clock(whole.end)}`)];
      requestAnimationFrame(() => reselect(body));
      return shown;
    });
  };
  return details;
}

// One event in full: the page shows the event, and a bar pinned below it steps
// through the events of its sequence (a session, or the linear view's rows),
// oldest first. The bar is kept while the sequence is the same, so its buttons
// stay where they are under the pointer.
let steps = null;  // the bar of the open event: its nav, sequence, event id and buttons
function stepBar(sequence, detail) {
  const previous = el("button", { className: "more" }, icon("left", 14), "Earlier");
  const next = el("button", { className: "more" }, "Later", icon("right", 14));
  const count = el("span", { className: "num" });
  const bar = { nav: el("nav", { className: "steps", ariaLabel: "Earlier and later events" }, previous, count, next),
                sequence, id: null, previous, next, count };
  const step = (by) => {
    const events = sequence.events();
    const index = events.findIndex((e) => e.id === bar.id) + by;
    if (events[index]) showEvent(sequence, events[index].id, detail);
  };
  previous.onclick = () => step(-1);
  next.onclick = () => step(1);
  return bar;
}
function showEvent(sequence, id, detail) {
  const button = sequence.row?.(id) ?? document.querySelector(`[data-key="${CSS.escape(id)}"]`);
  if (button) {
    reveal(button);
    select(button);
    button.scrollIntoView({ block: "nearest" });
  }
  if (!steps || steps.sequence !== sequence || steps.nav.parentNode !== detail) {
    steps = stepBar(sequence, detail);
    detail.replaceChildren(el("div", { className: "scroll" }), steps.nav);
  }
  const events = sequence.events();
  const index = events.findIndex((e) => e.id === id);
  steps.id = id;
  steps.previous.disabled = index === 0;
  steps.next.disabled = index === events.length - 1;
  steps.count.textContent = `${index + 1} of ${events.length}`;
  const page = detail.firstChild;
  page.replaceChildren(eventPage(events[index], sequence.note()));
  page.scrollTop = 0;
}
// The arrow keys step too, while an event is open and nothing is being typed.
document.addEventListener("keydown", (event) => {
  if (!steps?.nav.isConnected || event.altKey || event.ctrlKey || event.metaKey || event.target.closest?.("input, textarea")) return;
  const button = event.key === "ArrowLeft" ? steps.previous : event.key === "ArrowRight" ? steps.next : null;
  if (!button || button.disabled) return;
  event.preventDefault();
  button.click();
});

// The screenshot in its reserved box: the box alone while the image is on its
// way (a shimmer once that takes long enough to notice), then the image whole.
function screenshot(event) {
  const img = el("img", { className: "shot", alt: `Screenshot at ${clock(event.timestamp, true)}`, src: url(event.screenshot_url) });
  const box = el("div", { className: "shot-box" }, img);
  img.onload = () => box.classList.add("loaded");
  img.onerror = () => box.replaceWith(status("The screenshot could not be loaded."));
  return box;
}

const inputGrid = (inputs, event) => el("div", { className: "inputs" }, ...inputs.flatMap((input) => {
  const [what, details] = inputLine(input);
  return [el("span", { className: "dim", textContent: offset(input, event) }), el("b", { textContent: what }),
          el("span", { textContent: details })];
}));

function eventPage(event, note) {
  const shown = shownInputs(event.inputs);
  const folded = foldedNote(event.inputs);
  return el("div", { className: "page" },
    el("p", { className: "meta", textContent: [time(event.timestamp), event.device_id, ...(note ? [note] : [])].join(" · ") }),
    el("h1", { className: event.caption == null ? "none" : "", textContent: event.caption ?? "No caption yet" }),
    event.screenshot_url ? screenshot(event) : status("No screenshot with this event."),
    shown.length ? inputGrid(shown, event) : status("No keys, clicks or scrolls in this event."),
    ...(folded ? [el("details", { className: "folded" }, el("summary", {}, icon("right", 14), folded),
                     inputGrid(event.inputs.filter((input) => !shown.includes(input)), event))] : []));
}

// Predictions

const runLabel = (run) => (run.time ? run.time.toLocaleString(undefined,
  { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : run.run);

// The predictions pane: the graded runs, most recently graded first.
export function predictionsPane(pane, detail) {
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
