// Settings forms. A description, as juno.settings and GET /settings give it, is
// {sections, environment}: each section a dotted path, what it is and its
// fields; each field its kind, value, default and what it does. `sectionForm`
// draws one section as a form whose Save sends {set, reset} by dotted path;
// `environmentList` shows what is read from the environment, an API key only as
// set or not. The pages choose which sections to show and where a change goes.
import { el, messageOf } from "./views.js";

// A field's value as its input shows it: empty for null, JSON for a list or pair.
const shown = (field, value) => (value == null ? "" : field.kind === "json" ? JSON.stringify(value) : String(value));

// The value an input holds, as the field's kind; throws a message for the user.
function readValue(field, control) {
  if (field.kind === "bool") return control.checked;
  // A number field holding what is not a number reads as empty; say what it needs.
  if (control.validity?.badInput) throw new Error(field.kind === "int" ? "A whole number" : "A number");
  const text = control.value.trim();
  if (text === "") {
    if (field.nullable) return null;
    if (field.kind === "string") return "";
    throw new Error("Needs a value");
  }
  if (field.kind === "int" || field.kind === "float") {
    const n = Number(text);
    if (!Number.isFinite(n) || (field.kind === "int" && !Number.isInteger(n))) throw new Error(field.kind === "int" ? "A whole number" : "A number");
    return n;
  }
  if (field.kind === "json") {
    try {
      return JSON.parse(text);
    } catch {
      throw new Error("Not JSON, e.g. [640, 360]");
    }
  }
  return text;
}

function controlOf(field, id) {
  if (field.kind === "bool") return el("input", { id, type: "checkbox", className: "sw", checked: Boolean(field.value) });
  if (field.kind === "enum") {
    const options = [...(field.nullable ? [el("option", { value: "", textContent: "Not set" })] : []),
                     ...field.options.map((option) => el("option", { value: option, textContent: option }))];
    const select = el("select", { id }, ...options);
    select.value = shown(field, field.value);
    return select;
  }
  return el("input", {
    id, type: field.kind === "int" || field.kind === "float" ? "number" : "text", step: "any", spellcheck: false,
    value: shown(field, field.value), placeholder: field.nullable ? "Not set" : "",
  });
}

const leaf = (path) => path.slice(path.lastIndexOf(".") + 1);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// One field: its name, what it does, its control, its default when it differs,
// and a line for why a save refused it. `changed()` is called on every edit.
function fieldRow(field, changed) {
  const id = `setting-${field.path.replaceAll(".", "-")}`;
  const control = controlOf(field, id);
  const error = el("small", { className: "error", hidden: true });
  const defaultLine = el("small", { className: "default" });
  let reset = false;
  const reading = () => {
    try {
      return { value: readValue(field, control) };
    } catch (problem) {
      return { problem: problem.message };
    }
  };
  const drawDefault = () => {
    const read = reading();
    const atDefault = !read.problem && same(read.value, field.default);
    defaultLine.replaceChildren(...(atDefault ? [] : [
      `Default: ${field.default == null ? "not set" : shown(field, field.default) || "empty"} `,
      el("button", { type: "button", className: "link", textContent: "Use default", onclick: () => {
        if (field.kind === "bool") control.checked = Boolean(field.default);
        else control.value = shown(field, field.default);
        reset = true;
        drawDefault();
        changed();
      } })]));
  };
  control.addEventListener(field.kind === "bool" || field.kind === "enum" ? "change" : "input", () => {
    reset = false;
    error.hidden = true;
    drawDefault();
    changed();
  });
  drawDefault();
  const row = el("div", { className: "setting" },
    el("label", { htmlFor: id }, el("b", { textContent: leaf(field.path) }), el("code", { textContent: field.path })),
    control,
    ...(field.description ? [el("small", { className: "help", textContent: field.description })] : []),
    defaultLine, error);
  return {
    row,
    // {set: value} for a value other than the saved one, {reset: true} to go back
    // to the default, {problem} for one that cannot be read, or null if unchanged.
    change() {
      const read = reading();
      if (read.problem) return { problem: read.problem };
      if (reset && same(read.value, field.default)) return same(field.value, field.default) ? null : { reset: true };
      return same(read.value, field.value) ? null : { set: read.value };
    },
    showError(message) {
      error.textContent = message;
      error.hidden = false;
    },
  };
}

// The form of one section. `save({set, reset})` resolves to the new description
// or rejects with an error whose `errors` maps paths to why; `saved(description)`
// is called after a save. `note` (text or nodes) is shown beside the Save button,
// e.g. when a change just saved applies.
export function sectionForm(section, save, saved, note = "") {
  const submit = el("button", { type: "submit", className: "save", textContent: "Save", disabled: true });
  const line = el("small", { className: "saved", role: "status" });
  const rows = [];
  const changed = () => {
    submit.disabled = !rows.some((row) => row.change() !== null);
    line.textContent = "";
  };
  for (const field of section.fields) rows.push({ field, ...fieldRow(field, changed) });
  const plain = rows.filter((row) => !row.field.advanced).map((row) => row.row);
  const advanced = rows.filter((row) => row.field.advanced).map((row) => row.row);
  // noValidate: each field is read and refused with its reason here, never by the browser in silence.
  const form = el("form", { className: "settings", noValidate: true },
    ...plain,
    ...(advanced.length ? [el("details", { className: "advanced" }, el("summary", { textContent: `Advanced (${advanced.length})` }), ...advanced)] : []),
    el("footer", {}, submit, line));
  form.onsubmit = async (event) => {
    event.preventDefault();
    const change = { set: {}, reset: [] };
    let unreadable = false;
    for (const row of rows) {
      const c = row.change();
      if (!c) continue;
      if (c.problem) {
        row.showError(c.problem);
        unreadable = true;
      } else if (c.reset) change.reset.push(row.field.path);
      else change.set[row.field.path] = c.set;
    }
    if (unreadable) return;
    submit.disabled = true;
    line.textContent = "Saving…";
    try {
      const description = await save(change);
      saved(description);
    } catch (error) {
      const errors = error.errors ?? {};
      for (const row of rows) if (errors[row.field.path]) row.showError(errors[row.field.path]);
      const unplaced = Object.entries(errors).filter(([path]) => !rows.some((row) => row.field.path === path));
      line.textContent = !error.errors ? messageOf(error)
        : unplaced.length ? unplaced.map(([path, why]) => `${path}: ${why}`).join(" · ") : "Not saved; see the fields above";
      submit.disabled = false;
    }
  };
  if (note) line.replaceChildren(...[].concat(note));
  return form;
}

// What the process reads from its environment (.env), one row each: set or not,
// the value unless it is a secret, and which settings need it.
export function environmentList(environment) {
  return el("ul", { className: "environment" }, ...environment.map((v) => el("li", {},
    el("span", { className: `light ${v.set ? "recording" : v.needed_by.length ? "changing" : "idle"}` }),
    el("div", {},
      el("b", { textContent: v.name }),
      el("small", { textContent: v.set ? (v.secret ? "Set" : v.value) : v.needed_by.length ? `Not set; needed by ${v.needed_by.join(", ")}` : "Not set" }),
      el("small", { className: "help", textContent: v.description })))));
}

// A save function for `sectionForm` that calls `patch(change)` and turns its
// {description} or {errors} answer into the description or a rejection.
export const bridgedSave = (patch) => async (change) => {
  const result = await patch(change);
  if (result.errors) throw Object.assign(new Error("Not saved"), { errors: result.errors });
  return result.description;
};
