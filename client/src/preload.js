const { contextBridge, ipcRenderer } = require("electron");

// The renderer's only access to the main process. Add IPC wrappers here.
contextBridge.exposeInMainWorld("juno", {
  sendChat: (chatId, content) => ipcRenderer.invoke("chat:send", chatId, content),
  get: (route) => ipcRenderer.invoke("api:get", route),
  // For URLs the page loads itself (screenshots).
  base: (process.env.JUNO_SERVER_URL || "http://127.0.0.1:8765").replace(/\/$/, ""),
});
