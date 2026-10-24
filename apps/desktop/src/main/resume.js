// Reopens an assistant conversation from the event whose moment carries it, in
// a terminal or in the Claude desktop app (`./terminal`), on this computer only:
// the conversation's directory and transcript are on the device that recorded
// the event. This computer's device id is the recorder's
// (`juno.config.resolve_device_id`), asked of the checkout's Python once.
const { execFile, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { ipcMain } = require("electron");

const { resumeCommand, terminalEnv } = require("./terminal");

// Resolves to this computer's device id, or null if the recorder's Python cannot say.
function deviceIdOf(repoDir) {
  const python = path.join(repoDir, ".venv/bin/python");
  const script = "from juno.config import resolve_device_id; print(resolve_device_id())";
  return new Promise((resolve) => {
    execFile(python, ["-c", script], { cwd: repoDir }, (error, stdout) => resolve(error ? null : stdout.trim()));
  });
}

// Starts `command` on its own, past this app's exit; resolves once it has started.
function launch({ command, args }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { detached: true, stdio: "ignore", env: terminalEnv(process.env) });
    child.once("spawn", () => { child.unref(); resolve(); });
    child.once("error", reject);
  });
}

function registerResume(repoDir) {
  const deviceId = deviceIdOf(repoDir);
  ipcMain.handle("device:id", () => deviceId);
  // Resolves to {} once the terminal or the link's opener has started, or {error} saying why it was not.
  ipcMain.handle("conversation:resume", async (_event, eventDeviceId, conversation) => {
    if (eventDeviceId !== (await deviceId)) return { error: `This conversation is on ${eventDeviceId}.` };
    const command = resumeCommand(process.platform, conversation);
    if (!command) return { error: "This conversation cannot be reopened on this computer." };
    if (command.cwd && !fs.existsSync(command.cwd)) return { error: `${command.cwd} is no longer on this computer.` };
    try {
      await launch(command);
      return {};
    } catch (error) {
      return { error: error.code === "ENOENT" ? command.missing : error.message };
    }
  });
}

module.exports = { registerResume };
