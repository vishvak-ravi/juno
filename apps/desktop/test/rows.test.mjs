import assert from "node:assert/strict";
import { test } from "node:test";

import {
  actions, chips, day, dayLabel, duration, foldedNote, gridEdges, groupRuns, hourOf, inputLine, liveSessions,
  matches, modelName, monthGrid, offset, overallState, railItems, runTime, shade, shownInputs, weekStart, weekdays,
} from "../src/renderer/rows.mjs";

const event = (session, row, timestamp, fields = {}) => ({
  id: `${session}-${String(row).padStart(6, "0")}`, device_id: session.split("-")[0], timestamp,
  screenshot_url: null, inputs: [], caption: null, ...fields,
});

test("the linear timeline marks each new hour and names the device where it changes", () => {
  const at = (hour, minute) => new Date(2026, 9, 21, hour, minute).getTime() / 1000;
  const rows = [
    event("tablet-session_20261021_163420", 1, at(16, 40)),
    event("laptop-session_20261021_152109", 9, at(16, 35)),
    event("laptop-session_20261021_152109", 8, at(16, 5)),
    event("laptop-session_20261021_152109", 7, at(15, 55)),
  ];
  const shape = (items) => items.map((i) => (i.hour != null ? `hour ${i.hour}` : `${i.event.timestamp}${i.named ? " named" : ""}`));
  assert.deepEqual(shape(railItems(rows)), [
    `hour ${at(16, 0)}`, `${at(16, 40)} named`, `${at(16, 35)} named`, `${at(16, 5)}`,
    `hour ${at(15, 0)}`, `${at(15, 55)} named`,
  ]);
  // A later page continues from the last row drawn: no new hour mark, no name for the same device.
  const older = [event("laptop-session_20261021_152109", 6, at(15, 50))];
  assert.deepEqual(shape(railItems(older, rows.at(-1))), [`${at(15, 50)}`]);
  assert.equal(hourOf(at(15, 55)), at(15, 0));
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
