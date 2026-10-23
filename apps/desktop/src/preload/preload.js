const { contextBridge, ipcRenderer } = require("electron");

const prefs = ipcRenderer.sendSync("prefs:values");

// The renderer's only access to the main process. Add IPC wrappers here.
contextBridge.exposeInMainWorld("juno", {
  sendChat: (chatId, content) => ipcRenderer.invoke("chat:send", chatId, content),
  get: (route) => ipcRenderer.invoke("api:get", route),
  patch: (route, body) => ipcRenderer.invoke("api:patch", route, body),
  // This computer's recorder: {running, exitCode, log}, log being its last lines.
  captureStatus: () => ipcRenderer.invoke("capture:status"),
  // The three kinds of settings, each described as juno.settings describes them
  // ({sections, environment}); each patch takes {set, reset} by dotted path and
  // resolves to {description} once saved, or {errors} by path.
  // This app's own (settings.js), read once at load as `prefs` too.
  prefs,
  // The server URL, for URLs the page loads itself (screenshots).
  base: prefs.serverUrl,
  describePrefs: () => ipcRenderer.invoke("prefs:describe"),
  patchPrefs: (change) => ipcRenderer.invoke("prefs:patch", change),
  // This computer's recorder's (device.js); saving restarts the recorder.
  describeDeviceSettings: () => ipcRenderer.invoke("device-settings:describe"),
  patchDeviceSettings: (change) => ipcRenderer.invoke("device-settings:patch", change),
  // Quit and start again, for what applies at startup (the passive agent module).
  relaunch: () => ipcRenderer.invoke("app:relaunch"),
  // This computer's device id, the one its recorder uploads under; null if unknown.
  deviceId: () => ipcRenderer.invoke("device:id"),
  // Reopen the conversation of a moment (its `conversation`) recorded on eventDeviceId;
  // resolves to {} once a terminal has opened, or {error}.
  resume: (eventDeviceId, conversation) => ipcRenderer.invoke("conversation:resume", eventDeviceId, conversation),
  // Open (true) or close (false) a dictation segment; onDictation receives
  // {text, final} for it, text being the whole segment so far.
  dictate: (on) => ipcRenderer.send("dictation:set", on),
  onDictation: (callback) => ipcRenderer.on("dictation:text", (_event, update) => callback(update)),
  // Whether the passive agent module (camera, gestures, dictation) was on when the
  // app started: passed by main.js as --juno-passive-agent=1 or 0.
  passiveAgent: process.argv.includes("--juno-passive-agent=1"),
});
