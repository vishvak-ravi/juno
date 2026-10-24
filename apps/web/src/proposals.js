// What the phone page and the desktop window read the same way from the
// proposals routes' shapes: a proposal's light and words from its state, the
// line under its title, the feed's order and day groups, the ledger's lines. No DOM.

// The lists the feed is filtered by, as GET /proposals?state= names them, with the colour of each light.
export const BUCKETS = [["open", "Open", "var(--ink)"], ["accepted", "Accepted", "var(--green)"],
                        ["almost", "Almost", "var(--amber)"], ["dismissed", "Dismissed", "var(--faint)"]];

export const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
export const monthDay = (seconds) => new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
// A run's day, "YYYY-MM-DD", as a heading.
export const dayName = (day) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
export const shortDay = (day) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
export const score = (value) => (value == null ? "–" : value.toFixed(2));
// "openai/cyankiwi/gemma" → "cyankiwi/gemma".
export const modelName = (model) => model.split("/").slice(1).join("/") || model;

// The light a state is drawn with: its state, or almost when a no came after reading or editing.
export const lightOf = (state) => (state.almost ? "almost" : state.state);
const DECIDED = { accepted: "accepted", dismissed: "dismissed", snoozed: "later" };
// The words beside the light: "almost, dismissed" or "seen · read 38 s" or "new".
export function stateWords(state) {
  if (state.decided) return `${state.almost ? "almost, " : ""}${DECIDED[state.decided.event]}`;
  return state.state === "seen" ? `seen · read ${Math.round(state.dwell)} s` : "new";
}
// The line under a listed proposal's title: when it was decided and where, or how long it was read.
export function trailLine(state) {
  if (state.decided) return `${stateWords(state)} ${monthDay(state.decided.at)}, ${clock(state.decided.at)} · ${state.decided.where}`;
  return stateWords(state);
}
// The line under a proposal's title in the feed: its decision and when, else nothing (the light says new or seen).
export const feedLine = (state) => (state.decided ? `${stateWords(state)} · ${monthDay(state.decided.at)}, ${clock(state.decided.at)}` : "");
// The feed's rows under their task, in the rows' order: the two actions of one task sit together.
export function groupByTask(rows) {
  const groups = [];
  for (const row of rows) {
    const key = `${row.run_id}-${row.task.index}`;
    const group = groups.find((g) => g.key === key);
    if (group) group.rows.push(row); else groups.push({ key, task: row.task, rows: [row] });
  }
  return groups;
}
// What one event of a trail says.
export function eventWords(d) {
  switch (d.event) {
    case "listed": return "shown";
    case "opened": return `opened, read ${Math.round(d.dwell ?? 0)} s`;
    case "expanded": return `expanded ${d.detail ?? ""}`.trim();
    case "edited": return "edited the action";
    case "accepted": return "accepted";
    case "dismissed": return d.detail ? `dismissed: “${d.detail}”` : "dismissed";
    case "snoozed": return d.detail ? `later: “${d.detail}”` : "later";
    case "reopened": return "reopened";
    default: return d.event;
  }
}
// The light an event is drawn with in a trail.
export const eventLight = (d) => ({ accepted: "accepted", dismissed: "dismissed", snoozed: "snoozed", edited: "almost" }[d.event] ?? "seen");

// When a listed proposal last moved: its decision, else its run.
export const latestOf = (p) => p.state.decided?.at ?? p.created_at;
// The feed's order: what moved last first, then by id.
export const sortNewest = (rows) => [...rows].sort((a, b) => latestOf(b) - latestOf(a) || (a.id < b.id ? 1 : -1));
// The feed under a heading per day, in the rows' order.
export function groupByDay(rows) {
  const groups = [];
  for (const row of rows) {
    if (groups.at(-1)?.day !== row.day) groups.push({ day: row.day, rows: [] });
    groups.at(-1).rows.push(row);
  }
  return groups;
}
// The rows of one list, or every row.
export const inBucket = (rows, buckets) => rows.filter((r) => buckets.has(r.bucket));

// A task's place against the run's line: asked, almost asked, or not asked.
export function taskPlace(task, run) {
  if (task.asked) return task.asked_by === "user" ? "asked by you" : "asked";
  if (task.score == null) return "not scored";
  const under = run.utility_threshold - task.score;
  return under <= run.near_threshold ? "almost asked" : "not asked";
}
// The ledger's one number: "1 of 10 accepted".
export const rateLine = (rate) => `${rate.accepted} of ${rate.shown} accepted`;
// What a run came to, in a line.
export const runLine = (run) => `${plural(run.proposals, "proposal")} from ${run.actions.toLocaleString()} actions · ${run.asked} of ${run.tasks} tasks asked`
  + (run.seconds == null ? "" : ` · ${run.seconds.toFixed(1)} s`);
// The decisions of many proposals as one list, newest first: each event with its proposal.
export function ledgerEvents(proposals) {
  return proposals.flatMap((p) => (p.trail ?? []).filter((d) => d.event !== "listed").map((d) => ({ ...d, proposal: p })))
    .sort((a, b) => b.at - a.at);
}
// The proposal ids of a run's tasks, in task and action order.
export const proposalIds = (run) => run.tasks.flatMap((t) => t.actions.map((_, n) => `${run.id}-${String(t.index).padStart(2, "0")}-${n}`));
// The calls of a run that are about one task, those the proposer made last.
export const taskCalls = (calls, task) => calls.filter((c) => c.what === `task ${task.index + 1}` || c.what.startsWith(`task ${task.index + 1},`));
export const tokensOf = (calls) => calls.reduce((n, c) => n + (c.prompt_tokens ?? 0) + (c.completion_tokens ?? 0), 0);
