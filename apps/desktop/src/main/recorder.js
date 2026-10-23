// The recorder (`python -m capture`, see docs/capture.md) is a child of this app:
// it starts with it, stops with it, and the app quits only once it has saved its
// session. It sends to the server address saved in this app's settings
// (JUNO_SERVER_URL in its environment) and reads this computer's device.json; a
// change to either restarts it. It is not restarted when it exits on its own.
// Exit status 2 means its backend is unavailable, on macOS a permission not
// granted to the app this one was launched from; the window's devices panel
// shows the status and the last lines it logged.
const { spawn } = require("node:child_process");
const readline = require("node:readline");
const { app, ipcMain } = require("electron");

const { checkoutPython } = require("./python");

const RECORDER_LOG_LINES = 20;

// Starts the recorder from repoDir, with repoDir's .venv and .env, sending to
// serverUrl(). Returns {restart}, which stops it (letting it save its session)
// and starts it again.
function startCapture(repoDir, serverUrl) {
  const python = checkoutPython(repoDir);
  const recorder = { running: false, exitCode: null, log: [] };
  let child = null;

  const log = (line) => {
    process.stderr.write(`${line}\n`);
    recorder.log = [...recorder.log, line].slice(-RECORDER_LOG_LINES);
  };
  function spawnRecorder() {
    const env = { ...process.env, JUNO_SERVER_URL: serverUrl() };
    const started = spawn(python, ["-m", "capture"], { cwd: repoDir, env, stdio: ["ignore", "inherit", "pipe"] });
    child = started;
    Object.assign(recorder, { running: true, exitCode: null, log: [] });
    readline.createInterface({ input: started.stderr }).on("line", log);
    started.on("error", (error) => {
      if (child === started) recorder.running = false;
      log(`recorder: ${error.message}`);
    });
    started.on("exit", (code, signal) => {
      if (child !== started) return;
      recorder.running = false;
      recorder.exitCode = code ?? signal;
    });
  }

  // Stops the running recorder; resolves once it has saved its session and exited.
  const stopped = () => new Promise((resolve) => {
    if (!recorder.running || !child) return resolve();
    child.once("exit", () => resolve());
    child.kill();
  });

  let restarting = Promise.resolve();
  const restart = () => {
    restarting = restarting.then(stopped).then(spawnRecorder);
    return restarting;
  };

  spawnRecorder();
  ipcMain.handle("capture:status", () => ({ ...recorder }));
  let quitting = false;
  app.on("will-quit", (event) => {
    if (quitting || !recorder.running) return;
    event.preventDefault();
    quitting = true;
    stopped().then(() => app.quit());
  });
  return { restart };
}

module.exports = { startCapture };
