// The desktop app's own settings, set in the window's settings panel and kept
// in settings.json in Electron's userData directory, outside the checkout. The
// one setting is serverUrl, the app server the window reads from; the recorder
// uploads to the server named in the checkout's .env either way.
const fs = require("node:fs");
const path = require("node:path");
const { app, ipcMain } = require("electron");

const settingsFile = () => path.join(app.getPath("userData"), "settings.json");

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsFile(), "utf8"));
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

// An http(s) address without its trailing slash; throws with a message for the user otherwise.
function serverUrlOf(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${value || "An empty address"} is not an address`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("The address must start with http:// or https://");
  return url.href.replace(/\/$/, "");
}

// Registers the settings IPC and returns a getter of the server URL: the saved
// one, else defaultUrl. The preload reads it synchronously, so a reloaded page
// starts on the address just saved.
function registerSettings(defaultUrl) {
  let serverUrl = readSettings().serverUrl ?? defaultUrl;
  ipcMain.on("settings:server-url", (event) => { event.returnValue = serverUrl; });
  // Resolves to {url} once saved, or {error} saying why the address was refused.
  ipcMain.handle("settings:set-server-url", (_event, value) => {
    let url;
    try {
      url = serverUrlOf(String(value).trim());
    } catch (error) {
      return { error: error.message };
    }
    writeSettings({ ...readSettings(), serverUrl: url });
    serverUrl = url;
    return { url };
  });
  return () => serverUrl;
}

module.exports = { registerSettings };
