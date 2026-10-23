import assert from "node:assert/strict";
import { test } from "node:test";

import {
  actions, conversationsOf, day, dayBlocks, dayLabel, daysFrom, foldedNote, gridEdges, groupRuns, indexCurve, inputLine,
  liveSessions, matches, modelName, momentAt, momentText, monthGrid, offset, overallState, pinClusters, railLines, runTime, sequencesOf, shade,
  shownInputs, weekOf, weekStart, weekdays,
} from "../src/renderer/rows.mjs";

const event = (session, row, timestamp, fields = {}) => ({
  id: `${session}-${String(row).padStart(6, "0")}`, device_id: session.split("-")[0], timestamp,
  screenshot_url: null, inputs: [], caption: null, sequence: Math.floor(row / 2), sequence_caption: null, ...fields,
});

test("a session's events group into the sequences they were captioned in, oldest first", () => {
  const rows = [event("laptop-s", 0, 1), event("laptop-s", 1, 2, { sequence_caption: "A terminal." }),
                event("laptop-s", 2, 3, { sequence_caption: "A browser." }), event("laptop-s", 4, 5)];
  const sequences = sequencesOf(rows);
  assert.deepEqual(sequences.map((s) => [s.index, s.caption, s.events.length]), [[0, null, 2], [1, "A browser.", 1], [2, null, 1]]);
});

test("the moment rail lists each session's moments newest first, with a divider where the day changes", () => {
  const at = (date, hour) => new Date(2026, 9, date, hour).getTime() / 1000;
  const moment = (timestamp, type = "code_landed") => ({ type, label: null, timestamp, event_id: "e" });
  const session = (start, end, moments) => ({ id: `s${start}`, device_id: "laptop", start, end, moments });
  const lines = railLines([
    session(at(22, 17), at(22, 19), [moment(at(22, 18)), moment(at(22, 18.5))]),
    session(at(22, 9), at(22, 16), [moment(at(22, 12))]),
    session(at(20, 10), at(20, 11), []),
    session(at(19, 21), at(19, 22), [moment(at(19, 21.5))]),
  ]);
  const shape = lines.map((line) => (line.divider ? "|" : line.moment.timestamp));
  assert.deepEqual(shape, [at(22, 18.5), at(22, 18), at(22, 12), "|", "|", at(19, 21.5)]);
});

test("an action belongs to the latest moment starting at or before it", () => {
  const moment = (timestamp) => ({ type: "code_landed", label: null, timestamp, event_id: "e" });
  const moments = [moment(10), moment(20), moment(30)];
  assert.equal(momentAt(moments, 5), null);
  assert.equal(momentAt(moments, 10), moments[0]);
  assert.equal(momentAt(moments, 25), moments[1]);
  assert.equal(momentAt(moments, 99), moments[2]);
  assert.equal(momentAt([], 99), null);
});

test("moments closer than a pin share one, in the colour most of them have", () => {
  const moment = (timestamp, type) => ({ type, label: null, timestamp, event_id: "e" });
  const pins = pinClusters([moment(0, "code_landed"), moment(20, "message_sent"), moment(30, "message_sent"),
                            moment(200, "web_search_submitted"), moment(260, "web_search_submitted")], 60);
  assert.deepEqual(pins.map((p) => [p.timestamp, p.moments.length, p.type]),
                   [[0, 3, "message_sent"], [200, 2, "web_search_submitted"]]);
  assert.deepEqual(pinClusters([], 60), []);
});

test("a moment's divider names the assistant when one is known", () => {
  const moment = (type, label = null) => ({ type, probability: 0.9, detector: "jev", label });
  assert.equal(momentText(moment("agent_prompt_submitted", "codex")), "Prompt sent to Codex");
  assert.equal(momentText(moment("agent_prompt_submitted", "other")), "Prompt sent");
  assert.equal(momentText(moment("agent_needs_input")), "Assistant waiting for your answer");
  assert.equal(momentText(moment("tab_switched")), "tab switched");
});

test("an index's curve breaks where a bucket is missing or has no value, and is smoothed over its neighbours", () => {
  const bucket = (start, value) => ({ start, rows: 1, detected: value == null ? 0 : 1, values: { agent: value } });
  const indices = { names: ["agent"], bucket_seconds: 100,
                    buckets: [bucket(0, 0.2), bucket(100, 0.4), bucket(200, 0.6), bucket(400, null), bucket(500, 0.1)] };
  const runs = indexCurve(indices, "agent", 0, 1000, 1);
  assert.deepEqual(runs.map((run) => run.map((p) => p.y)), [[0.2, 0.4, 0.6], [0.1]]);
  assert.deepEqual(runs[0].map((p) => p.x), [0.05, 0.15, 0.25]);
  const smooth = indexCurve(indices, "agent", 0, 1000, 3);
  assert.deepEqual(smooth[0].map((p) => p.y.toFixed(2)), ["0.30", "0.40", "0.50"]);
});

test("a session is drawn on each day it touches, as fractions of that day", () => {
  const days = daysFrom(new Date(2026, 9, 19), 3);
  const at = (date, hour) => new Date(2026, 9, date, hour).getTime() / 1000;
  const blocks = dayBlocks({ start: at(20, 18), end: at(21, 6) }, days);
  assert.deepEqual(blocks.map((b) => [b.day, b.from, +b.to.toFixed(3)]), [[1, 0.75, 1], [2, 0, 0.25]]);
  assert.deepEqual(dayBlocks({ start: at(23, 1), end: at(23, 2) }, days), []);
});

test("a week starts on the locale's first day and a calendar's days run from it", () => {
  assert.deepEqual(weekOf(new Date(2026, 9, 22, 15), 0), new Date(2026, 9, 18));
  assert.deepEqual(weekOf(new Date(2026, 9, 22, 15), 1), new Date(2026, 9, 19));
  assert.deepEqual(daysFrom(new Date(2026, 9, 30), 3), [new Date(2026, 9, 30), new Date(2026, 9, 31), new Date(2026, 10, 1)]);
});

test("inputs shown are keys, clicks and scrolls; releases and pointer moves are folded away", () => {
  const inputs = [
    { type: "key_press", timestamp: 1, key: "a" }, { type: "key_release", timestamp: 1.1, key: "a" },
    { type: "key_press", timestamp: 2, key: "b" }, { type: "mouse_move", timestamp: 3, x: 1, y: 1 },
    { type: "mouse_down", timestamp: 4, button: "Button.left", x: 1, y: 1 },
    { type: "mouse_up", timestamp: 4.1, button: "Button.left", x: 1, y: 1 },
  ];
  assert.equal(shownInputs(inputs).length, 3);
  assert.equal(foldedNote(inputs), "2 releases and 1 pointer move");
  assert.equal(foldedNote(shownInputs(inputs)), null);
});

test("an input reads as what it was, without the pointer where it was not read", () => {
  assert.deepEqual(inputLine({ type: "mouse_down", button: "Button.left", x: 640, y: 400 }), ["Click", "left · 640, 400"]);
  assert.deepEqual(inputLine({ type: "mouse_down", button: "Button.left", x: null, y: null }), ["Click", "left"]);
  assert.deepEqual(inputLine({ type: "mouse_scroll", dx: 0, dy: -3, x: null, y: null }), ["Scroll", "0, -3"]);
  assert.equal(offset({ timestamp: 10.21 }, { timestamp: 10 }), "+0.21 s");
});

test("a day runs from local midnight to just before the next", () => {
  const { since, until, start } = day(new Date(2026, 9, 19, 15, 30));
  assert.equal(since, new Date(2026, 9, 19).getTime() / 1000);
  assert.ok(until < new Date(2026, 9, 20).getTime() / 1000 && until > new Date(2026, 9, 19, 23, 59).getTime() / 1000);
  assert.deepEqual(start, new Date(2026, 9, 19));
});

test("a day is named relative to now", () => {
  const now = new Date(2026, 9, 20, 15, 30);
  assert.equal(dayLabel(new Date(2026, 9, 20), now), "Today");
  assert.equal(dayLabel(new Date(2026, 9, 19), now), "Yesterday");
  assert.equal(dayLabel(new Date(2026, 9, 5), now), new Date(2026, 9, 5).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }));
});

test("a month's calendar is six weeks from the week start before the first, shaded by count", () => {
  const sundays = monthGrid(2026, 9, 0);
  assert.equal(sundays.length, 42);
  assert.deepEqual(sundays[0], new Date(2026, 8, 27));
  assert.deepEqual(sundays[4], new Date(2026, 9, 1));
  assert.deepEqual(monthGrid(2026, 9, 1)[0], new Date(2026, 8, 28));
  const edges = gridEdges(sundays);
  assert.equal(edges.length, 43);
  assert.equal(edges[42], new Date(2026, 10, 8).getTime() / 1000);
  assert.equal(shade(0, 9), 0);
  assert.equal(shade(9, 9), 1);
  assert.ok(shade(1, 9) > 0.3 && shade(1, 9) < 0.34);
  assert.equal(weekStart("en-US"), 0);
  assert.equal(weekStart("not a locale"), 0);
  assert.deepEqual(weekdays(1, "en-US"), ["M", "T", "W", "T", "F", "S", "S"]);
});

test("action lists are read from the XML and compared position by position", () => {
  const predicted = actions("<actions>\n  <action>Clicks the tab.</action>\n  <action>Types &amp; saves.</action>\n</actions>");
  const actual = actions("<actions><action>Clicks the tab.</action><action>Closes it.</action></actions>");
  assert.deepEqual(predicted, ["Clicks the tab.", "Types & saves."]);
  assert.deepEqual(matches(predicted, actual), [true, false]);
  assert.deepEqual(actions(null), []);
});

test("graded predictions are grouped by run, in prediction order, with mean reward and failures", () => {
  const graded = (run, index, reward, error = null) => ({
    id: `s-${run}-${index}`, run, index, reward, error,
    predictor: { model: "hosted_vllm/local-model" }, grader: { model: "gemini/gemini-flash" },
  });
  const [later, earlier] = groupRuns([graded("20261004_100000", 1, 0.5), graded("20261004_100000", 0, 0.7),
                                      graded("20261004_100000", 2, null, "timeout"), graded("20261003_183000", 0, 0.4)]);
  assert.deepEqual(later.rows.map((g) => g.index), [0, 1, 2]);
  assert.equal(later.mean.toFixed(2), "0.60");
  assert.equal(later.failed, 1);
  assert.equal(later.predictor, "local-model");
  assert.equal(later.grader, "gemini-flash");
  assert.equal(earlier.run, "20261003_183000");
  assert.deepEqual(runTime("20261004_100000"), new Date(2026, 9, 4, 10, 0, 0));
  assert.equal(runTime("not-a-run"), null);
  assert.equal(modelName("plain"), "plain");
});

test("the devices light reads the whole of /control", () => {
  const device = (capture, running, online = true) => ({
    capture, online, reported: { capture: running ? "running" : "stopped", session_id: running ? "laptop-session_1" : null },
  });
  const control = (devices) => ({ devices });
  assert.equal(overallState(null, null), "offline");
  assert.equal(overallState(control({ laptop: device(true, true) }), { running: true }), "recording");
  assert.equal(overallState(control({ laptop: device(true, false) }), null), "changing");
  assert.equal(overallState(control({ laptop: device(true, true) }), { running: false }), "changing");
  assert.equal(overallState(control({ laptop: device(false, false), desk: device(true, true, false) }), null), "idle");
  assert.deepEqual([...liveSessions(control({ laptop: device(true, true) }))], ["laptop-session_1"]);
});

test("an event's conversations are those its hook-reported moments carry, each once", () => {
  const conversation = (session_id) => ({ harness: "claude_code", session_id, cwd: "/work" });
  const reported = (session_id) => ({ type: "agent_prompt_submitted", probability: 1, detector: "claude_code_hook",
                                      label: "claude_code", conversation: conversation(session_id) });
  const guessed = { type: "error_shown", probability: 0.9, detector: "jev", label: null, conversation: null };
  const e = event("laptop-session_20261021_152109", 0, 0, { moments: [reported("a"), guessed, reported("b"), reported("a")] });
  assert.deepEqual(conversationsOf(e), [conversation("a"), conversation("b")]);
  assert.deepEqual(conversationsOf(event("laptop-session_20261021_152109", 1, 0)), []);
});
