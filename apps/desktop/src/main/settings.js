// The desktop app's own settings (prefs.js), kept in settings.json in Electron's
// userData directory, outside the checkout: read once at startup and on every
// change, written only here.
const fs = require("node:fs");
const path = require("node:path");
const { app, ipcMain } = require("electron");

const { defaultsOf, describe, patched, valuesOf } = require("./prefs");

const settingsFile = () => path.join(app.getPath("userData"), "settings.json");

function readSettings() {
  try {
    const saved = JSON.parse(fs.readFileSync(settingsFile(), "utf8"));
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) {
      console.error(`${settingsFile()} does not hold an object and is ignored`);
      return {};
    }
    return saved;
  } catch (error) {
    if (error.code === "ENOENT") return {};
    if (error instanceof SyntaxError) {
      console.error(`${settingsFile()} is not JSON and is ignored: ${error.message}`);
      return {};
    }
    throw error;
  }
}

function writeSettings(settings) {
  fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
  fs.writeFileSync(settingsFile(), `${JSON.stringify(settings, null, 2)}\n`);
}

// Registers the settings IPC; returns a getter of the current values.
// `onServerUrl(url)` is called after a change of the server address.
function registerSettings(defaultServerUrl, onServerUrl) {
  const defaults = defaultsOf(defaultServerUrl);
  let values = valuesOf(readSettings(), defaults);
  // The preload reads them synchronously, so a reloaded page starts on what was just saved.
  ipcMain.on("prefs:values", (event) => { event.returnValue = values; });
  ipcMain.handle("prefs:describe", () => describe(values, defaults));
  // Resolves to {description} once saved, or {errors} by dotted path.
  ipcMain.handle("prefs:patch", (_event, change) => {
    const result = patched(readSettings(), defaults, change ?? {});
    if (result.errors) return { errors: result.errors };
    writeSettings(result.saved);
    const before = values;
    values = valuesOf(result.saved, defaults);
    if (values.serverUrl !== before.serverUrl) onServerUrl(values.serverUrl);
    return { description: describe(values, defaults) };
  });
  // Quits (once the recorder has saved its session) and starts again, for what applies at startup.
  ipcMain.handle("app:relaunch", () => {
    app.relaunch();
    app.quit();
  });
  return () => values;
}

module.exports = { registerSettings };
