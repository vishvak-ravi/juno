// The checkout's config.yaml, for the renderer (window.juno.config): asked of the
// checkout's Python once at startup (`juno.config.load_config`), so the client
// reads the same file, with the same defaults, as the recorder it starts.
const { execFile } = require("node:child_process");
const path = require("node:path");
const { ipcMain } = require("electron");

// Resolves to the config as JSON, or null if the checkout's Python cannot read it.
function configOf(repoDir) {
  const python = path.join(repoDir, ".venv/bin/python");
  const script = "from juno.config import load_config; print(load_config().model_dump_json())";
  return new Promise((resolve) => {
    execFile(python, ["-c", script], { cwd: repoDir }, (error, stdout) => {
      if (error) return resolve(null);
      try {
        resolve(JSON.parse(stdout));
      } catch {
        resolve(null);
      }
    });
  });
}

function registerConfig(repoDir) {
  const config = configOf(repoDir);
  ipcMain.handle("config:get", () => config);
}

module.exports = { registerConfig };
