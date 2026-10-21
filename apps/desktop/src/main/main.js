const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const { startCapture } = require("./recorder");
const { startDictation } = require("./dictation");
const { registerServerApi } = require("./server");

// The checkout: config.yaml and .env are read from it and .venv holds the recorder.
const REPO_DIR = path.resolve(__dirname, "../../../..");

// JUNO_SERVER_HOST is read from the checkout's .env, the file the server listens on
// and the recorder uploads by, so the address is set in one place. Variables
// already in the environment win.
try {
  process.loadEnvFile(path.join(REPO_DIR, ".env"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

// JUNO_PASSIVE_AGENT=1 turns on the passive agent module: the camera preview with
// hand keypoints, the gesture roles (dictate, send) and the dictation helper. Unset
// or 0 leaves the pages with the composer only.
const PASSIVE_AGENT = process.env.JUNO_PASSIVE_AGENT === "1";

// The Juno app server; JUNO_SERVER_URL overrides the one built from the host.
const SERVER_URL = (
  process.env.JUNO_SERVER_URL || `http://${process.env.JUNO_SERVER_HOST || "127.0.0.1"}:8765`
).replace(/\/$/, "");

function createWindow() {
  const window = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // The sandboxed preload reads the server URL and the passive agent switch from its argv.
      additionalArguments: [`--juno-server-url=${SERVER_URL}`, `--juno-passive-agent=${PASSIVE_AGENT ? 1 : 0}`],
    },
  });
  window.loadFile(path.join(__dirname, "../renderer/index.html"));
}

app.whenReady().then(() => {
  registerServerApi(SERVER_URL);
  startCapture(REPO_DIR);
  if (PASSIVE_AGENT) startDictation();
  createWindow();
  // macOS: re-create a window when the dock icon is clicked and none are open.
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
