const path = require("node:path");
const { app, BrowserWindow, ipcMain } = require("electron");

// The Juno app server, e.g. http://<tailscale-host>:8765.
const SERVER_URL = (process.env.JUNO_SERVER_URL || "http://127.0.0.1:8765").replace(/\/$/, "");

// PUT /chats/{id} appends the messages plus the model's reply and returns the
// whole chat. Sent from the main process so the server needs no CORS setup.
ipcMain.handle("chat:send", async (_event, chatId, content) => {
  const response = await fetch(`${SERVER_URL}/chats/${encodeURIComponent(chatId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages: [{ role: "user", content }] }),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
});

// GET any server route, for the browser page (browse.html); same reason.
ipcMain.handle("api:get", async (_event, route) => {
  const response = await fetch(SERVER_URL + route);
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
});

function createWindow() {
  const window = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // JUNO_PAGE=browse.html opens the chat/session browser instead of the chat.
  window.loadFile(path.join(__dirname, process.env.JUNO_PAGE || "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  // macOS: re-create a window when the dock icon is clicked and none are open.
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
