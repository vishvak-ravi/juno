const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");
const { app, BrowserWindow, ipcMain } = require("electron");

// JUNO_SERVER_HOST is read from the app's .env, the file the server listens on
// and the recorder uploads by, so the address is set in one place. Variables
// already in the environment win.
try {
  process.loadEnvFile(path.join(__dirname, "../../app/.env"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

// The Juno app server; JUNO_SERVER_URL overrides the one built from the host.
const SERVER_URL = (
  process.env.JUNO_SERVER_URL || `http://${process.env.JUNO_SERVER_HOST || "127.0.0.1"}:8765`
).replace(/\/$/, "");

// PUT /chats/{id} appends the messages plus the model's reply and returns the
// whole chat. Sent from the main process so the server needs no CORS setup.
ipcMain.handle("chat:send", async (_event, chatId, content) => {
  const response = await fetch(`${SERVER_URL}/chats/${encodeURIComponent(chatId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages: [{ role: "user", content }] }),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
});

// GET any server route, for the browser page (browse.html); same reason.
ipcMain.handle("api:get", async (_event, route) => {
  const response = await fetch(SERVER_URL + route);
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
});

// PATCH a server route with a JSON body, for the switches on the status page; same reason.
ipcMain.handle("api:patch", async (_event, route, body) => {
  const response = await fetch(SERVER_URL + route, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
});

// The recorder (`python -m capture` in app/, see docs/capture.md) is a child of
// this app: it starts with it, stops with it, and the app quits only once it has
// saved its session. It is not restarted when it exits. Exit status 2 means its
// backend is unavailable, on macOS a permission not granted to the app this one
// was launched from; the Status page shows the status and the last lines it logged.
const APP_DIR = path.join(__dirname, "../../app");
const PYTHON = path.join(APP_DIR, ".venv/bin/python");
const RECORDER_LOG_LINES = 20;
const recorder = { running: false, exitCode: null, log: [] };

function startCapture() {
  const child = spawn(PYTHON, ["-m", "capture"], { cwd: APP_DIR, stdio: ["ignore", "inherit", "pipe"] });
  recorder.running = true;
  const log = (line) => {
    process.stderr.write(`${line}\n`);
    recorder.log = [...recorder.log, line].slice(-RECORDER_LOG_LINES);
  };
  readline.createInterface({ input: child.stderr }).on("line", log);
  child.on("error", (error) => {
    recorder.running = false;
    log(`recorder: ${error.message}`);
  });
  child.on("exit", (code, signal) => {
    recorder.running = false;
    recorder.exitCode = code ?? signal;
  });
  ipcMain.handle("capture:status", () => ({ ...recorder }));
  let quitting = false;
  app.on("will-quit", (event) => {
    if (quitting || !recorder.running) return;
    event.preventDefault();
    quitting = true;
    child.once("exit", () => app.quit());
    child.kill();
  });
}

// The dictation helper (native/dictate.swift, built by `npm run native`) takes
// "start"/"stop" lines and prints one JSON line per recognition update.
function startDictation() {
  const helper = spawn(path.join(__dirname, "../native/juno-dictate"), {
    stdio: ["pipe", "pipe", "inherit"],
  });
  helper.on("error", (error) => console.error(`dictation helper: ${error.message}`));
  helper.stdin.on("error", () => {}); // the helper exited; its stderr says why
  readline.createInterface({ input: helper.stdout }).on("line", (line) => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.send("dictation:text", JSON.parse(line));
    }
  });
  ipcMain.on("dictation:set", (_event, on) => helper.stdin.write(on ? "start\n" : "stop\n"));
  app.on("will-quit", () => helper.kill());
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // The sandboxed preload reads the server URL from its argv.
      additionalArguments: [`--juno-server-url=${SERVER_URL}`],
    },
  });
  // JUNO_PAGE=browse.html opens the chat/session browser instead of the chat.
  window.loadFile(path.join(__dirname, process.env.JUNO_PAGE || "index.html"));
}

app.whenReady().then(() => {
  startCapture();
  startDictation();
  createWindow();
  // macOS: re-create a window when the dock icon is clicked and none are open.
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
