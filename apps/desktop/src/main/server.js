// The renderer's calls to the Juno app server, made from the main process so the
// server needs no CORS setup.
const { ipcMain } = require("electron");

async function checked(response) {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
}

// Registers the IPC handlers against the server at serverUrl(), read on every call.
function registerServerApi(serverUrl) {
  // PUT /chats/{id} appends the messages plus the model's reply and returns the whole chat.
  ipcMain.handle("chat:send", (_event, chatId, content) =>
    fetch(`${serverUrl()}/chats/${encodeURIComponent(chatId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: [{ role: "user", content }] }),
    }).then(checked),
  );

  // GET any server route, for the page's lists and views.
  ipcMain.handle("api:get", (_event, route) => fetch(serverUrl() + route).then(checked));

  // PATCH a server route with a JSON body, for the switches in the devices panel.
  ipcMain.handle("api:patch", (_event, route, body) =>
    fetch(serverUrl() + route, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(checked),
  );
}

module.exports = { registerServerApi };
