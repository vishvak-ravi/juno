// The settings view: this app's settings, this computer's recorder's and the
// server's, listed in the left pane by section with what each reads from the
// environment, and the open one's form in the detail pane. All three are read
// together, so the list arrives at once; a source that cannot be read says why
// in its place. A save answers with the source's new description, which is
// drawn in place, with when the change applies.
import { api, patch } from "../../../web/src/api.js";
import { bridgedSave, environmentList, sectionForm } from "../../../web/src/settings.js";
import { el, load, messageOf, status } from "../../../web/src/views.js";
import { select } from "./browse.js";

// The first sentence of a section's description, for its row.
const firstSentence = (text) => (text ?? "").split(/(?<=\.)\s/)[0];

// PATCH /settings, its refusal turned into errors by path as the form expects.
async function saveServer(change) {
  try {
    return await patch("/settings", change);
  } catch (error) {
    if (error.status === 422 && error.detail && typeof error.detail === "object") throw Object.assign(error, { errors: error.detail });
    throw error;
  }
}

// Each source: its heading, how it is read and saved, and what to say after a save.
const SOURCES = [
  {
    key: "app", title: "This app",
    read: () => window.juno.describePrefs(),
    save: (change) => bridgedSave(window.juno.patchPrefs)(change),
    available: () => Boolean(window.juno?.describePrefs),
    after: (description) => {
      const values = Object.fromEntries(description.sections[0].fields.map((f) => [f.path, f.value]));
      if (values["app.serverUrl"] !== window.juno.prefs.serverUrl) location.reload();
      if (values["app.passiveAgent"] !== window.juno.passiveAgent) {
        return [el("span", { textContent: "Saved. The passive agent module changes when Juno restarts. " }),
                el("button", { type: "button", className: "link", textContent: "Restart Juno", onclick: () => window.juno.relaunch() })];
      }
      return "Saved.";
    },
  },
  {
    key: "device", title: "This computer",
    read: () => window.juno.describeDeviceSettings(),
    save: (change) => bridgedSave(window.juno.patchDeviceSettings)(change),
    available: () => Boolean(window.juno?.describeDeviceSettings),
    after: () => "Saved. The recorder restarted with these settings.",
  },
  {
    key: "server", title: "Server",
    read: () => api("/settings"),
    save: saveServer,
    available: () => true,
    after: (description) => (description.restart.length ? "Saved. It applies when the server restarts." : "Saved."),
  },
];

export function settingsPane(pane, detail) {
  const list = el("div", { className: "scroll" });
  pane.replaceChildren(el("header", {}, el("h2", { textContent: "Settings" })), list);
  const sources = SOURCES.filter((source) => source.available());
  // What each source last answered: {description} or {error}.
  const read = {};
  let openKey = null;

  function page(source, item, note) {
    const description = read[source.key].description;
    const heading = el("header", { className: "page-head" }, el("h2", { textContent: `${source.title} · ${item.title}` }));
    if (item.environment) {
      return [heading, el("p", { className: "lede", textContent: "Read from the .env file of the machine it runs on; change them there and restart it." }),
              environmentList(description.environment)];
    }
    const section = description.sections.find((s) => s.path === item.path);
    const saved = (next) => {
      read[source.key] = { description: next };
      drawList();
      open(source, item, source.after(next));
    };
    return [heading, ...(section.description ? [el("p", { className: "lede", textContent: section.description })] : []),
            sectionForm(section, source.save, saved, note)];
  }

  function open(source, item, note) {
    openKey = `${source.key}:${item.path}`;
    const button = list.querySelector(`[data-key="${CSS.escape(openKey)}"]`);
    if (button) select(button);
    const body = el("div", { className: "settings-page" });
    body.replaceChildren(...page(source, item, note));
    detail.replaceChildren(el("div", { className: "scroll" }, body));
  }

  function items(source) {
    const description = read[source.key].description;
    return [...description.sections.map((s) => ({ path: s.path, title: s.path, note: firstSentence(s.description) })),
            ...(description.environment.length ? [{ path: "environment", title: "Environment", environment: true,
                                                    note: `${description.environment.filter((v) => v.set).length} of ${description.environment.length} set` }] : [])];
  }

  function group(source) {
    const answer = read[source.key];
    const restart = answer.description?.restart?.length
      ? [el("small", { className: "restart", textContent: `${answer.description.restart.length} saved changes apply when the server restarts` })] : [];
    const heading = el("h3", { className: "group-head", textContent: source.title });
    if (answer.error) return [heading, status(answer.error)];
    const rows = items(source).map((item) => {
      const button = el("button", { className: "row" }, el("b", { textContent: item.title }), el("small", { textContent: item.note }));
      button.dataset.key = `${source.key}:${item.path}`;
      button.onclick = () => open(source, item);
      return el("li", {}, button);
    });
    return [heading, ...restart, el("ul", {}, ...rows)];
  }

  function drawList() {
    list.replaceChildren(...sources.flatMap(group));
  }

  load(list, async () => {
    const answers = await Promise.allSettled(sources.map((source) => source.read()));
    sources.forEach((source, i) => {
      read[source.key] = answers[i].status === "fulfilled"
        ? { description: answers[i].value } : { error: `${source.title}: ${messageOf(answers[i].reason)}` };
    });
    return sources.flatMap(group);
  }).then(() => {
    // The first section that could be read opens, so the detail pane shows settings too.
    const source = sources.find((s) => read[s.key]?.description);
    if (source && !openKey) open(source, items(source)[0]);
  });
}
