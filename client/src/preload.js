const { contextBridge, ipcRenderer } = require("electron");

// The renderer's only access to the main process. Add IPC wrappers here.
contextBridge.exposeInMainWorld("juno", {
  sendChat: (chatId, content) => ipcRenderer.invoke("chat:send", chatId, content),
  get: (route) => ipcRenderer.invoke("api:get", route),
  // Open (true) or close (false) a dictation segment; onDictation receives
  // {text, final} for it, text being the whole segment so far.
  dictate: (on) => ipcRenderer.send("dictation:set", on),
  onDictation: (callback) => ipcRenderer.on("dictation:text", (_event, update) => callback(update)),
  // For URLs the page loads itself (screenshots).
  base: (process.env.JUNO_SERVER_URL || "http://127.0.0.1:8765").replace(/\/$/, ""),
});
