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
  // The server URL, for URLs the page loads itself (screenshots): the one saved
  // in settings (settings.js), else the one main.js builds from .env.
  base: ipcRenderer.sendSync("settings:server-url"),
  // Save a server URL; resolves to {url}, or {error} if it is not an http(s) address.
  // The page reloads to read from it.
  setServerUrl: (url) => ipcRenderer.invoke("settings:set-server-url", url),
  // Whether the passive agent module (camera, gestures, dictation) is on: JUNO_PASSIVE_AGENT,
  // passed by main.js as --juno-passive-agent=1 or 0.
  passiveAgent: process.argv.includes("--juno-passive-agent=1"),
});
