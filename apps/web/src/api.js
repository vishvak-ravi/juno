// The server's routes, called the same way from every page: through the desktop
// app's bridge (window.juno, whose requests its main process makes) when there is
// one, else by fetch against the address the page was opened at.

export class ApiError extends Error {
  constructor(status, detail) {
    super(`${status} ${detail}`);
    this.status = status;
  }
}

const bridge = window.juno;
// Where the server is: the bridge says; a served page uses its own origin; a page
// opened from disk without the bridge tries the server on this computer.
export const BASE = bridge?.base ?? (location.protocol === "file:" ? "http://127.0.0.1:8765" : "");
const JSON_HEADERS = { "Content-Type": "application/json" };

// Stand-in responses by path, shown when the server cannot be reached.
let examples = {};
export const useExamples = (byPath) => { examples = byPath; };

const detailOf = (text) => {
  try {
    const detail = JSON.parse(text).detail ?? text;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch {
    return text;
  }
};

async function checked(response) {
  if (!response.ok) throw new ApiError(response.status, detailOf(await response.text()));
  return response.json();
}

// The bridge rejects with "<status> <body>" as one message; give it back its status.
const bridged = (promise) => promise.catch((error) => {
  const m = /^(\d{3}) ([\s\S]*)$/.exec(error.message || "");
  throw m ? new ApiError(Number(m[1]), detailOf(m[2])) : error;
});

async function fetched(path, options) {
  try {
    return await checked(await fetch(BASE + path, options));
  } catch (error) {
    if (!(path in examples)) throw error;
    if (!document.title.endsWith("(example data)")) document.title += " (example data)";
    return examples[path];
  }
}

// GET a route.
export const api = (path) => (bridge ? bridged(bridge.get(path)) : fetched(path));

// PATCH a route with a JSON body (the switches of /control).
export const patch = (path, body) => (bridge
  ? bridged(bridge.patch(path, body))
  : fetched(path, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body) }));

// PUT /chats/{id}: appends the message and the model's reply, creating the chat if new.
export const send = (chatId, content) => (bridge
  ? bridged(bridge.sendChat(chatId, content))
  : fetched(`/chats/${chatId}`, {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ messages: [{ role: "user", content }] }),
  }));
