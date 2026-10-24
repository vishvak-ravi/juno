// What the lattice pane (`latticePane` in browse.js) computes from the lattice
// routes' shapes: the layers' names, where a node was made, a layer's nodes
// under the day, week, range or session they were made in, a build's stages. No DOM.
import { clock, dayLabel } from "./rows.mjs";

// What a layer's spec calls its nodes; layer 0 is the observations.
const SPEC_NAMES = { day: "Day", week: "Week", month: "Month", year: "Year", session: "Session", number: "Group", all: "Overall" };
export const layerName = (spec) => (spec == null ? "Observation" : SPEC_NAMES[spec.type === "time" ? spec.value : spec.type] ?? "Insight");
// The names of every layer of a lattice, layer 0 first, and the same as the layer switch shows them.
export const layerNames = (lattice) => [null, ...lattice.layers.map((l) => l.spec)].map(layerName);
export const layerPlural = (spec) => (spec == null ? "Observations" : spec.type === "all" ? "Overall" : `${layerName(spec)}s`);

// A node's time, as the build wrote it ("YYYY-MM-DD HH:MM:SS", local), as seconds.
export function nodeTime(node) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(node.metadata?.time ?? "");
  return m ? new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() / 1000 : null;
}
export const monthDay = (seconds) => new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
// The Monday a time's week starts on: the build groups a week layer by ISO week.
const weekOf = (seconds) => {
  const d = new Date(seconds * 1000);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7)).getTime() / 1000;
};
// The range a build was read over, e.g. "Oct 12 – 22".
export function rangeLabel(since, until) {
  if (since == null || until == null) return "every caption";
  const a = new Date(since * 1000), b = new Date((until - 1) * 1000);
  return a.getMonth() === b.getMonth() ? `${monthDay(since)} – ${b.getDate()}` : `${monthDay(since)} – ${monthDay(until - 1)}`;
}
// Where a node was made: an observation's session, an insight's day, ISO week or the build's range,
// from the node's time and its layer's spec.
export function groupLabel(spec, node, build, now = new Date()) {
  const t = nodeTime(node);
  if (t == null || (spec != null && spec.type === "all")) return rangeLabel(build.since, build.until);
  if (spec == null) return `${dayLabel(new Date(t * 1000), now)}, ${clock(t)}`;
  if (spec.type === "time" && spec.value === "day") return dayLabel(new Date(t * 1000), now);
  if (spec.type === "time" && spec.value === "week") return rangeLabel(weekOf(t), weekOf(t) + 7 * 86400);
  if (spec.type === "time") return new Date(t * 1000).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  return dayLabel(new Date(t * 1000));
}

// The nodes of a layer, layer 0 being the observations.
export const layerNodes = (lattice, layer) => (layer === 0 ? lattice.observations : lattice.layers[layer - 1]?.insights ?? []);
const layerEdges = (lattice, layer) => lattice.layers[layer - 1]?.edges ?? [];
// A layer's nodes under the group each was made in, in the layer's order: an
// observation under its session (its day and span), an insight under its day,
// week or the build's range.
export function layerGroups(lattice, layer, now = new Date()) {
  const spec = layer ? lattice.layers[layer - 1].spec : null;
  const groups = [];
  for (const node of layerNodes(lattice, layer)) {
    let key, label;
    if (layer === 0) {
      key = `s${node.metadata.input_session}`;
      const session = lattice.build.sessions?.[node.metadata.input_session];
      label = session ? `${dayLabel(new Date(session.start * 1000), now)} · ${clock(session.start)} – ${clock(session.end)}`
        : groupLabel(null, node, lattice.build, now);
    } else {
      label = groupLabel(spec, node, lattice.build, now);
      key = label;
    }
    if (groups.at(-1)?.key !== key) groups.push({ key, label, nodes: [] });
    groups.at(-1).nodes.push(node);
  }
  return groups;
}
// The stages of a build as rows: name, nodes, seconds, failures; the share of the longest stage.
export function stageRows(build, names) {
  const stages = ["observe", ...build.node_counts.slice(1).map((_, i) => `layer_${i + 1}`)];
  const longest = Math.max(1, ...Object.values(build.stage_seconds));
  return stages.map((stage, i) => ({
    name: names[i] ?? stage, nodes: build.node_counts[i] ?? null, seconds: build.stage_seconds[stage] ?? null,
    failures: build.stage_failures[stage] ?? 0, share: (build.stage_seconds[stage] ?? 0) / longest,
  }));
}
// The session an event id belongs to.
export const sessionIdOf = (eventId) => eventId.slice(0, eventId.lastIndexOf("-"));
export const minutes = (s) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} m` : `${Math.floor(s / 60)} m ${Math.round(s % 60)} s`);
