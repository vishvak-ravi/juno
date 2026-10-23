const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const { registerDeviceSettings } = require("./device");
const { startCapture } = require("./recorder");
const { startDictation } = require("./dictation");
const { registerResume } = require("./resume");
const { registerServerApi } = require("./server");
const { registerSettings } = require("./settings");

// The checkout: .env is read from it and .venv holds the recorder.
const REPO_DIR = path.resolve(__dirname, "../../../..");

// This computer's environment (data directory, server host, device id) is read
// from the checkout's .env, the file the recorder and the server read too.
// Variables already in the environment win.
try {
  process.loadEnvFile(path.join(REPO_DIR, ".env"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

// The server address until the settings view saves another: JUNO_SERVER_URL, else
// built from JUNO_SERVER_HOST and JUNO_SERVER_PORT as juno.config.server_url builds it.
const DEFAULT_SERVER_URL = (
  process.env.JUNO_SERVER_URL
  || `http://${process.env.JUNO_SERVER_HOST || "127.0.0.1"}:${process.env.JUNO_SERVER_PORT || 8765}`
).replace(/\/$/, "");

function createWindow(passiveAgent) {
  const window = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // The sandboxed preload reads the passive agent switch from its argv.
      additionalArguments: [`--juno-passive-agent=${passiveAgent ? 1 : 0}`],
    },
  });
  window.loadFile(path.join(__dirname, "../renderer/index.html"));
}

app.whenReady().then(() => {
  let recorder = null;
  const prefs = registerSettings(DEFAULT_SERVER_URL, () => recorder.restart());
  const serverUrl = () => prefs().serverUrl;
  recorder = startCapture(REPO_DIR, serverUrl);
  registerServerApi(serverUrl);
  registerDeviceSettings(REPO_DIR, () => ({ ...process.env, JUNO_SERVER_URL: serverUrl() }), () => recorder.restart());
  registerResume(REPO_DIR);
  // The passive agent module is set up at startup; a change applies on relaunch.
  const passiveAgent = prefs().passiveAgent;
  if (passiveAgent) startDictation();
  createWindow(passiveAgent);
  // macOS: re-create a window when the dock icon is clicked and none are open.
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(passiveAgent);
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
