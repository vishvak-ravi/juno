// The renderer's calls to the Juno app server, made from the main process so the
// server needs no CORS setup. With JUNO_TIMING_FILE set, each GET appends one JSON
// line to that file: the route, the milliseconds to the response's headers and to
// its parsed body, the server's own milliseconds (its Server-Timing app;dur) and
// the body's bytes.
const fs = require("node:fs");
const { ipcMain } = require("electron");

const TIMING_FILE = process.env.JUNO_TIMING_FILE;

async function checked(response) {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
}

// The milliseconds of `app;dur=` in a Server-Timing header; null without one.
const serverMs = (header) => {
  const m = /(?:^|,)\s*app;dur=([\d.]+)/.exec(header || "");
  return m ? Number(m[1]) : null;
};

// GET url as checked() does, appending its timing line to TIMING_FILE.
async function timedGet(url, route) {
  const start = performance.now();
  const response = await fetch(url);
  const headersMs = performance.now() - start;
  const text = await response.text();
  if (!response.ok) throw new Error(`${response.status} ${text}`);
  const body = JSON.parse(text);
  const line = {
    at: Date.now() / 1000, route, status: response.status,
    headers_ms: headersMs, total_ms: performance.now() - start,
    server_ms: serverMs(response.headers.get("server-timing")), bytes: Buffer.byteLength(text),
  };
  fs.appendFile(TIMING_FILE, `${JSON.stringify(line)}\n`, (error) => {
    if (error) console.error(`${TIMING_FILE}: ${error.message}`);
  });
  return body;
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
  ipcMain.handle("api:get", (_event, route) => (TIMING_FILE
    ? timedGet(serverUrl() + route, route)
    : fetch(serverUrl() + route).then(checked)));

  // PATCH a server route with a JSON body, for the switches in the devices panel.
  ipcMain.handle("api:patch", (_event, route, body) =>
    fetch(serverUrl() + route, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(checked),
  );

  // POST a server route with a JSON body, for a proposal run started from the pane.
  ipcMain.handle("api:post", (_event, route, body) =>
    fetch(serverUrl() + route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(checked),
  );
}

module.exports = { registerServerApi };
