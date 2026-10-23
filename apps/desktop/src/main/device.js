// This computer's recorder settings (juno.config.DeviceSettings, kept in
// <data_dir>/device.json), read and changed through the checkout's Python
// (`python -m juno.settings device`), so the recorder's own models validate them.
// A saved change restarts the recorder.
const { ipcMain } = require("electron");

const { runPython } = require("./python");

// Runs `python -m juno.settings device ...args` with `input` on stdin; resolves to
// its JSON output: the description, or {errors} by dotted path.
async function runSettings(repoDir, env, args, input = "") {
  const { stdout, stderr, error } = await runPython(repoDir, ["-m", "juno.settings", "device", ...args], { env, input });
  try {
    return JSON.parse(stdout);
  } catch {
    throw new Error(stderr.trim() || error?.message || "The recorder's settings could not be read");
  }
}

// Registers the device settings IPC. `env()` is the recorder's environment, so the
// description shows the server address it sends to; `onSaved()` restarts it.
function registerDeviceSettings(repoDir, env, onSaved) {
  ipcMain.handle("device-settings:describe", () => runSettings(repoDir, env(), []));
  // Resolves to {description} once saved, or {errors} by dotted path.
  ipcMain.handle("device-settings:patch", async (_event, change) => {
    const result = await runSettings(repoDir, env(), ["patch"], JSON.stringify(change ?? {}));
    if (result.errors) return { errors: result.errors };
    await onSaved();
    return { description: result };
  });
}

module.exports = { registerDeviceSettings };
