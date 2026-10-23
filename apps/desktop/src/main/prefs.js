// The desktop app's own settings: what this window reads and how, as opposed to
// the server's (GET /settings) and this computer's recorder's (device.js). They
// are kept in settings.json in Electron's userData directory, holding only the
// values that differ from the defaults below, and described in the shape
// juno.settings describes the other two in, so the settings view draws all three
// with one form.

// Each setting: its key in settings.json, its kind as juno.settings names kinds,
// what it does, and a parser that returns the value to keep or throws a message
// for the user.
const FIELDS = [
  {
    key: "serverUrl", kind: "string",
    description: "The Juno server this window reads from and this computer's recorder sends to. Saving reloads the window and restarts the recorder.",
    parse: serverUrlOf,
  },
  {
    key: "prefetch", kind: "int",
    description: "Actions either side of the open one whose screenshots are fetched ahead, so stepping to them shows each at once.",
    parse: (value) => {
      const n = Number(value);
      if (!Number.isInteger(n) || n < 0) throw new Error("A whole number, 0 or more");
      return n;
    },
  },
  {
    key: "passiveAgent", kind: "bool",
    description: "The passive agent module: the camera preview with hand keypoints, the gesture roles (dictate, send) and dictation. Needs models/gesture_recognizer.task (npm run model) and, for dictation, native/juno-dictate (npm run native, macOS). Applies when Juno restarts.",
    parse: (value) => {
      if (typeof value !== "boolean") throw new Error("On or off");
      return value;
    },
  },
];

const SECTION = "app";
const pathOf = (key) => `${SECTION}.${key}`;

// An http(s) address without its trailing slash; throws with a message for the user otherwise.
function serverUrlOf(value) {
  let url;
  try {
    url = new URL(String(value).trim());
  } catch {
    throw new Error(`${value || "An empty address"} is not an address`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("The address must start with http:// or https://");
  return url.href.replace(/\/$/, "");
}

// The defaults: the server address from the checkout's .env (main.js), the rest fixed here.
const defaultsOf = (serverUrl) => ({ serverUrl, prefetch: 3, passiveAgent: false });

// The settings: `saved` (settings.json's contents) over the defaults, unknown keys dropped.
function valuesOf(saved, defaults) {
  const values = { ...defaults };
  for (const field of FIELDS) {
    if (!(field.key in saved)) continue;
    try {
      values[field.key] = field.parse(saved[field.key]);
    } catch {
      // A value this version cannot read keeps the default.
    }
  }
  return values;
}

// The settings in juno.settings' description shape: one section, every field.
function describe(values, defaults) {
  return {
    sections: [{
      path: SECTION,
      description: "This app's own settings, kept on this computer.",
      fields: FIELDS.map((field) => ({
        path: pathOf(field.key), kind: field.kind, nullable: false, options: null,
        description: field.description, advanced: false,
        default: defaults[field.key], value: values[field.key],
      })),
    }],
    environment: [],
  };
}

// Applies a {set, reset} change of dotted paths to `saved`. Returns {saved} (the
// new contents of settings.json, defaults left out) or {errors} by path.
function patched(saved, defaults, { set = {}, reset = [] }) {
  const byPath = new Map(FIELDS.map((field) => [pathOf(field.key), field]));
  const errors = {};
  const next = { ...saved };
  for (const path of reset) {
    if (byPath.has(path)) delete next[byPath.get(path).key];
    else errors[path] = "no such setting";
  }
  for (const [path, value] of Object.entries(set)) {
    const field = byPath.get(path);
    if (!field) {
      errors[path] = "no such setting";
      continue;
    }
    try {
      const parsed = field.parse(value);
      if (parsed === defaults[field.key]) delete next[field.key];
      else next[field.key] = parsed;
    } catch (error) {
      errors[path] = error.message;
    }
  }
  return Object.keys(errors).length ? { errors } : { saved: next };
}

module.exports = { defaultsOf, describe, patched, serverUrlOf, valuesOf };
