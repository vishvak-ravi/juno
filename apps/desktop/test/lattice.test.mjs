import assert from "node:assert/strict";
import { test } from "node:test";

import { groupLabel, layerGroups, layerNames, layerPlural, nodeTime, rangeLabel, sessionIdOf, stageRows } from "../src/renderer/lattice.mjs";

const observation = (id, time, session = 0) => ({ id, observation: `o${id}`, confidence: 5, metadata: { input_session: session, time } });
const insight = (id, merged, time = "2026-10-20 09:10:00") => ({ id, title: `i${id}`, tagline: "", insight: "", context: "", supporting_evidence: [], merged,
                                                                   metadata: { input_session: 0, time } });
const edges = (insights) => insights.flatMap((i) => i.merged.map((target) => ({ source: i.id, target })));
const LATTICE = {
  build: { since: new Date(2026, 9, 12).getTime() / 1000, until: new Date(2026, 9, 23).getTime() / 1000,
           sessions: [{ index: 0, start: new Date(2026, 9, 20, 9, 10).getTime() / 1000, end: new Date(2026, 9, 20, 12, 40).getTime() / 1000, interactions: [] }] },
  observations: [observation(0, "2026-10-20 09:10:00"), observation(1, "2026-10-20 09:10:00"), observation(2, "2026-10-22 08:50:00", 1)],
  layers: [
    { spec: { type: "time", value: "day" }, insights: [insight(0, [0, 1]), insight(1, [2], "2026-10-22 08:50:00")], edges: edges([insight(0, [0, 1]), insight(1, [2])]) },
    { spec: { type: "all" }, insights: [insight(0, [0, 1])], edges: edges([insight(0, [0, 1])]) },
  ],
};

test("a layer's nodes are listed under the day, week, range or session they were made in", () => {
  const now = new Date(2026, 9, 25);
  assert.deepEqual(layerGroups(LATTICE, 1, now).map((g) => [g.label, g.nodes.map((n) => n.id)]), [["Tue, Oct 20", [0]], ["Thu, Oct 22", [1]]]);
  assert.deepEqual(layerGroups(LATTICE, 2, now).map((g) => [g.label, g.nodes.map((n) => n.id)]), [["Oct 12 – 22", [0]]]);
  const [session, later] = layerGroups(LATTICE, 0, now);
  assert.match(session.label, /^Tue, Oct 20 · 9:10 AM – 12:40 PM$/);
  assert.deepEqual(session.nodes.map((n) => n.id), [0, 1]);
  assert.deepEqual([later.label, later.nodes.map((n) => n.id)], ["Thu, Oct 22, 8:50 AM", [2]]);
});

test("layers are named after their specs, the observations first", () => {
  assert.deepEqual(layerNames(LATTICE), ["Observation", "Day", "Overall"]);
  assert.deepEqual([null, ...LATTICE.layers.map((l) => l.spec)].map(layerPlural), ["Observations", "Days", "Overall"]);
});

test("a node is placed in its day, its ISO week or the build's range by its layer's spec", () => {
  const node = LATTICE.layers[0].insights[0];
  assert.equal(nodeTime(node), new Date(2026, 9, 20, 9, 10).getTime() / 1000);
  assert.equal(groupLabel({ type: "time", value: "day" }, node, LATTICE.build, new Date(2026, 9, 25)), "Tue, Oct 20");
  assert.equal(groupLabel({ type: "time", value: "week" }, node, LATTICE.build), "Oct 19 – 25");
  assert.equal(groupLabel({ type: "all" }, node, LATTICE.build), "Oct 12 – 22");
  assert.match(groupLabel(null, LATTICE.observations[2], LATTICE.build, new Date(2026, 9, 25)), /^Thu, Oct 22, 8:50/);
  assert.equal(rangeLabel(null, null), "every caption");
});

test("a build's stages line up with its layers, the longest stage filling its bar", () => {
  const build = { node_counts: [48, 18, 1], stage_seconds: { observe: 1200, layer_1: 600 }, stage_failures: { observe: 1 } };
  const rows = stageRows(build, ["Observe", "Days", "Overall"]);
  assert.deepEqual(rows.map((r) => [r.name, r.nodes, r.seconds, r.failures, r.share]),
    [["Observe", 48, 1200, 1, 1], ["Days", 18, 600, 0, 0.5], ["Overall", 1, null, 0, 0]]);
});

test("an event id names its session", () => {
  assert.equal(sessionIdOf("laptop-session_20261020_091003-000074"), "laptop-session_20261020_091003");
});
