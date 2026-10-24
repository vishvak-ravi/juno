import assert from "node:assert/strict";
import { test } from "node:test";

import {
  eventLight, eventWords, groupByDay, inBucket, latestOf, ledgerEvents, lightOf, proposalIds, rateLine, runLine, sortNewest, stateWords,
  taskCalls, taskPlace, trailLine,
} from "../../web/src/proposals.js";

const at = new Date(2026, 9, 22, 9, 48).getTime() / 1000;
const state = (fields) => ({ state: "new", almost: false, dwell: 0, edited: false, decided: null, ...fields });
const row = (id, day, created_at, s, bucket = "open") => ({ id, day, created_at, state: state(s), bucket });

test("a proposal's light and words follow its state, almost first", () => {
  assert.equal(lightOf(state({})), "new");
  assert.equal(lightOf(state({ state: "dismissed", almost: true })), "almost");
  assert.equal(stateWords(state({})), "new");
  assert.equal(stateWords(state({ state: "seen", dwell: 38.4 })), "seen · read 38 s");
  const no = state({ state: "snoozed", almost: true, decided: { event: "snoozed", at, where: "phone" } });
  assert.equal(stateWords(no), "almost, later");
  assert.match(trailLine(no), /^almost, later Oct 22, 9:48 AM · phone$/);
  assert.equal(trailLine(state({ state: "seen", dwell: 4 })), "seen · read 4 s");
});

test("each event of a trail has its words and light", () => {
  assert.equal(eventWords({ event: "opened", dwell: 12.6 }), "opened, read 13 s");
  assert.equal(eventWords({ event: "expanded", detail: "Rests on" }), "expanded Rests on");
  assert.equal(eventWords({ event: "dismissed", detail: "the GPU holds vLLM" }), "dismissed: “the GPU holds vLLM”");
  assert.equal(eventWords({ event: "snoozed" }), "later");
  assert.deepEqual(["edited", "accepted", "opened"].map((event) => eventLight({ event })), ["almost", "accepted", "seen"]);
});

test("the feed lists what moved last first, under a heading per day, in the lists chosen", () => {
  const a = row("r-00-0", "2026-10-22", 100, {});
  const b = row("r-00-1", "2026-10-22", 100, { state: "accepted", decided: { event: "accepted", at: 500, where: "desktop" } }, "accepted");
  const c = row("q-00-0", "2026-10-21", 50, { state: "dismissed", decided: { event: "dismissed", at: 60, where: "desktop" } }, "dismissed");
  assert.deepEqual([latestOf(a), latestOf(b), latestOf(c)], [100, 500, 60]);
  assert.deepEqual(sortNewest([c, a, b]).map((r) => r.id), ["r-00-1", "r-00-0", "q-00-0"]);
  assert.deepEqual(groupByDay(sortNewest([c, a, b])).map((g) => [g.day, g.rows.length]), [["2026-10-22", 2], ["2026-10-21", 1]]);
  assert.deepEqual(inBucket([a, b, c], new Set(["open", "dismissed"])).map((r) => r.id), ["r-00-0", "q-00-0"]);
});

test("a task is asked, almost asked or not asked against the run's line", () => {
  const run = { utility_threshold: 0.75, near_threshold: 0.1 };
  assert.equal(taskPlace({ asked: true, asked_by: "threshold", score: 0.8 }, run), "asked");
  assert.equal(taskPlace({ asked: true, asked_by: "user", score: 0.5 }, run), "asked by you");
  assert.equal(taskPlace({ asked: false, score: 0.7 }, run), "almost asked");
  assert.equal(taskPlace({ asked: false, score: 0.5 }, run), "not asked");
  assert.equal(taskPlace({ asked: false, score: null }, run), "not scored");
});

test("the ledger's lines and events", () => {
  assert.equal(rateLine({ shown: 10, accepted: 1 }), "1 of 10 accepted");
  assert.equal(runLine({ proposals: 6, actions: 1018, asked: 3, tasks: 4, seconds: 18.73 }), "6 proposals from 1,018 actions · 3 of 4 tasks asked · 18.7 s");
  const p = { id: "r-00-0", trail: [{ event: "listed", at: 1 }, { event: "opened", at: 2, dwell: 3 }, { event: "accepted", at: 5 }] };
  const q = { id: "r-00-1", trail: [{ event: "listed", at: 1 }, { event: "dismissed", at: 4 }] };
  assert.deepEqual(ledgerEvents([p, q]).map((e) => [e.event, e.proposal.id]), [["accepted", "r-00-0"], ["dismissed", "r-00-1"], ["opened", "r-00-0"]]);
});

test("a run's proposal ids and a task's calls", () => {
  const run = { id: "dawn_20261023_111512", tasks: [{ index: 0, actions: [{}, {}] }, { index: 1, actions: [] }, { index: 2, actions: [{}] }] };
  assert.deepEqual(proposalIds(run), ["dawn_20261023_111512-00-0", "dawn_20261023_111512-00-1", "dawn_20261023_111512-02-0"]);
  const calls = [{ step: "tasks", what: "window 1 of 7" }, { step: "utility", what: "task 1" }, { step: "utility", what: "task 10" },
                 { step: "actions", what: "task 1, asked by the user" }];
  assert.deepEqual(taskCalls(calls, { index: 0 }).map((c) => c.what), ["task 1", "task 1, asked by the user"]);
});
