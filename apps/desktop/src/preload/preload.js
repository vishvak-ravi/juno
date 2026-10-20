const { contextBridge, ipcRenderer } = require("electron");

// The renderer's only access to the main process. Add IPC wrappers here.
contextBridge.exposeInMainWorld("juno", {
  sendChat: (chatId, content) => ipcRenderer.invoke("chat:send", chatId, content),
  get: (route) => ipcRenderer.invoke("api:get", route),
  patch: (route, body) => ipcRenderer.invoke("api:patch", route, body),
  // This computer's recorder: {running, exitCode, log}, log being its last lines.
  captureStatus: () => ipcRenderer.invoke("capture:status"),
  // Open (true) or close (false) a dictation segment; onDictation receives
  // {text, final} for it, text being the whole segment so far.
  dictate: (on) => ipcRenderer.send("dictation:set", on),
  onDictation: (callback) => ipcRenderer.on("dictation:text", (_event, update) => callback(update)),
  // For URLs the page loads itself (screenshots).
  // main.js passes the server URL as --juno-server-url=<url>.
  base: process.argv.find((arg) => arg.startsWith("--juno-server-url=")).slice("--juno-server-url=".length),
  // Whether the passive agent module (camera, gestures, dictation) is on: JUNO_PASSIVE_AGENT,
  // passed by main.js as --juno-passive-agent=1 or 0.
  passiveAgent: process.argv.includes("--juno-passive-agent=1"),
});
