// The recorder (`python -m capture`, see docs/capture.md) is a child of this app:
// it starts with it, stops with it, and the app quits only once it has saved its
// session. It is not restarted when it exits. Exit status 2 means its backend is
// unavailable, on macOS a permission not granted to the app this one was launched
// from; the window's devices panel shows the status and the last lines it logged.
const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");
const { app, ipcMain } = require("electron");

const RECORDER_LOG_LINES = 20;
const recorder = { running: false, exitCode: null, log: [] };

// Starts the recorder from repoDir, with repoDir's .venv and its config.yaml and .env.
function startCapture(repoDir) {
  const python = path.join(repoDir, ".venv/bin/python");
  const child = spawn(python, ["-m", "capture"], { cwd: repoDir, stdio: ["ignore", "inherit", "pipe"] });
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

module.exports = { startCapture };
