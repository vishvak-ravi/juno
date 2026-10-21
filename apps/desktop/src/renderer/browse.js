// The timeline and predictions panes of the desktop browser, coarse to fine: the
// left pane lists one day's sessions (or the graded runs), a session opens to its
// events (a run to its predictions), and one event or prediction opens in full
// in the detail pane. A screenshot is requested only when its event is opened; a
// prediction's prompts and the predictor's screenshots only when it is opened and
// their section expanded.
import { api } from "../../../web/src/api.js";
import { el, load, messageOf, pres, predictorInput, problemsOf, score, status, time, url } from "../../../web/src/views.js";
import {
  actions, chips, clock, day, dayFromValue, dayLabel, dayValue, duration, foldedNote, groupRuns, groupSessions,
  inputLine, liveSessions, matches, modelName, offset, shownInputs,
} from "./rows.mjs";

// The routes' largest page.
const PAGE = 100;
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

// One session or run: a summary that opens to its rows, the first SHOWN_ROWS
// of them and a button for the rest.
function group(key, summary, items, moreLabel) {
  const list = el("ul", { className: "rows" }, ...items);
  if (items.length > SHOWN_ROWS) {
    items.slice(SHOWN_ROWS).forEach((li) => { li.hidden = true; });
    const reveal = el("button", { className: "reveal", textContent: moreLabel(items.length - SHOWN_ROWS) });
    const more = el("li", {}, reveal);
    reveal.onclick = () => { items.forEach((li) => { li.hidden = false; }); more.remove(); };
    list.append(more);
  }
  const details = el("details", { className: "group" }, el("summary", {}, ...summary, icon("down")), list);
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

// The timeline pane: the day shown, a button that opens the calendar to pick
// another, then that day's sessions oldest first. `control()` is the latest
// GET /control, for which sessions are being recorded.
export function timelinePane(pane, detail, control) {
  let shown = null;
  const label = el("span");
  const input = el("input", { type: "date", ariaLabel: "Day", ariaHidden: "true", tabIndex: -1, max: dayValue(new Date()) });
  const pick = el("button", { ariaLabel: "Pick a day" }, icon("calendar", 14), label);
  const list = el("div", { className: "scroll" });
  pane.replaceChildren(el("header", {}, el("h2", { textContent: "Timeline" }), el("span", { className: "day" }, pick, input)), list);
  const show = (date) => {
    shown = date;
    const { since, until, start } = day(date);
    label.textContent = dayLabel(start);
    input.value = dayValue(start);
    const live = liveSessions(control());
    load(list, async () => (await groupedPages(
      `/events?since=${since}&until=${until}&order=asc&limit=${PAGE}`, groupSessions,
      (session) => sessionGroup(session, live, detail), "Nothing recorded this day.")).nodes,
    () => shown === date);
  };
  // The calendar is the date input's own, opened from the button; where it cannot
  // be opened that way, the input itself takes the button's place.
  pick.onclick = () => {
    try {
      input.showPicker();
    } catch {
      pick.hidden = true;
      input.className = "shown";
      input.ariaHidden = null;
      input.tabIndex = 0;
      input.focus();
    }
  };
  input.onchange = () => { if (input.value) show(dayFromValue(input.value)); };
  show(new Date());
}

function sessionGroup(session, live, detail) {
  const n = session.events.length;
  const summary = [
    el("span", { className: `light ${live.has(session.id) ? "recording" : "idle"}` }),
    el("span", { className: "label" },
      el("b", { textContent: `${session.device} · ${clock(session.start)} – ${clock(session.end)}` }),
      el("small", { textContent: [duration(session.end - session.start), plural(n, "event"),
                                  session.captioned ? `${session.captioned} captioned` : "no captions yet"].join(" · ") })),
  ];
  const rows = session.events.map((event, index) => {
    const caption = el("span", { className: event.caption == null ? "caption none" : "caption",
                                 textContent: event.caption ?? "No caption yet" });
    const button = el("button", { className: "row event" },
      el("span", { className: "when", textContent: clock(event.timestamp, true) }),
      el("span", {}, caption, el("span", { className: "chips" },
        ...chips(event).map((chip) => el("span", { className: "chip", textContent: chip })))));
    button.dataset.key = event.id;
    button.onclick = () => showEvent(session, index, detail);
    return el("li", {}, button);
  });
  return group(session.id, summary, rows, (more) => `${more} more until ${clock(session.end)}`);
}

// One event in full: a bar pinned above the page steps through the events of
// its session, the page below it shows the event. The bar is kept while the
// session is the same, so its buttons stay where they are under the pointer.
let steps = null;  // the bar of the open event: its nav, session, index and buttons
function stepBar(session, detail) {
  const previous = el("button", { className: "more" }, icon("left", 14), "Previous");
  const next = el("button", { className: "more" }, "Next", icon("right", 14));
  const count = el("span", { className: "num" });
  const bar = { nav: el("nav", { className: "steps", ariaLabel: "Events of this session" }, previous, count, next),
                session, index: 0, previous, next, count };
  previous.onclick = () => showEvent(session, bar.index - 1, detail);
  next.onclick = () => showEvent(session, bar.index + 1, detail);
  return bar;
}
function showEvent(session, index, detail) {
  const button = document.querySelector(`[data-key="${CSS.escape(session.events[index].id)}"]`);
  if (button) {
    reveal(button);
    select(button);
  }
  if (!steps || steps.session !== session || steps.nav.parentNode !== detail) {
    steps = stepBar(session, detail);
    detail.replaceChildren(steps.nav, el("div", { className: "scroll" }));
  }
  steps.index = index;
  steps.previous.disabled = index === 0;
  steps.next.disabled = index === session.events.length - 1;
  steps.count.textContent = `${index + 1} of ${session.events.length}`;
  const page = detail.lastChild;
  page.replaceChildren(eventPage(session, index));
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

function eventPage(session, index) {
  const event = session.events[index];
  const shown = shownInputs(event.inputs);
  const folded = foldedNote(event.inputs);
  return el("div", { className: "page" },
    el("p", { className: "meta", textContent: `${time(event.timestamp)} · ${event.device_id} · session from ${clock(session.start)}` }),
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
  return group(run.run, summary, rows, (more) => `${more} more`);
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
