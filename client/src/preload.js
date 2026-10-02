const { contextBridge } = require("electron");

// The renderer's only access to the main process. Add IPC wrappers here.
contextBridge.exposeInMainWorld("juno", {
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
});
