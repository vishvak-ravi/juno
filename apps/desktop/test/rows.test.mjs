import assert from "node:assert/strict";
import { test } from "node:test";

import {
  actions, chips, day, dayFromValue, dayLabel, dayValue, duration, foldedNote, groupRuns, groupSessions, inputLine,
  liveSessions, matches, modelName, offset, overallState, runTime, shownInputs,
} from "../src/renderer/rows.mjs";

const event = (session, row, timestamp, fields = {}) => ({
  id: `${session}-${String(row).padStart(6, "0")}`, device_id: session.split("-")[0], timestamp,
  screenshot_url: null, inputs: [], caption: null, ...fields,
});

test("events of one page are grouped by session in page order, with span and caption counts", () => {
  const sessions = groupSessions([
    event("laptop-session_20261003_091500", 0, 100, { caption: "Opens the editor." }),
    event("laptop-session_20261003_091500", 1, 160),
    event("tablet-session_20261003_093000", 0, 200, { caption: "Draws a box." }),
    event("laptop-session_20261003_091500", 2, 400, { caption: "Saves." }),
  ]);
  assert.deepEqual(sessions.map((s) => [s.id, s.device, s.events.length, s.start, s.end, s.captioned]), [
    ["laptop-session_20261003_091500", "laptop", 3, 100, 400, 2],
    ["tablet-session_20261003_093000", "tablet", 1, 200, 200, 1],
  ]);
});

test("chips count what the user did and leave out releases and pointer moves", () => {
  const inputs = [
    { type: "key_press", timestamp: 1, key: "a" }, { type: "key_release", timestamp: 1.1, key: "a" },
    { type: "key_press", timestamp: 2, key: "b" }, { type: "mouse_move", timestamp: 3, x: 1, y: 1 },
    { type: "mouse_down", timestamp: 4, button: "Button.left", x: 1, y: 1 },
    { type: "mouse_up", timestamp: 4.1, button: "Button.left", x: 1, y: 1 },
  ];
  assert.deepEqual(chips({ inputs, screenshot_url: "/s" }), ["2 keys", "1 click", "screenshot"]);
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
  assert.equal(duration(41 * 60), "41 min");
  assert.equal(duration(226 * 60), "3 h 46 min");
});

test("a day is named relative to now and round-trips through a date input's value", () => {
  const now = new Date(2026, 9, 20, 15, 30);
  assert.equal(dayLabel(new Date(2026, 9, 20), now), "Today");
  assert.equal(dayLabel(new Date(2026, 9, 19), now), "Yesterday");
  assert.equal(dayLabel(new Date(2026, 9, 5), now), new Date(2026, 9, 5).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }));
  assert.equal(dayValue(new Date(2026, 0, 5)), "2026-01-05");
  assert.deepEqual(dayFromValue("2026-01-05"), new Date(2026, 0, 5));
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
