// The dictation helper (native/dictate.swift, built by `npm run native`) takes
// "start"/"stop" lines and prints one JSON line per recognition update.
const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");
const { app, BrowserWindow, ipcMain } = require("electron");

function startDictation() {
  const helper = spawn(path.join(__dirname, "../../native/juno-dictate"), {
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

module.exports = { startDictation };
