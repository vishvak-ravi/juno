// One chat per window; the server creates it on the first message.
const chatId = crypto.randomUUID();

const messages = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const button = form.querySelector("button");

function render(list) {
  messages.replaceChildren(
    ...list.map(({ role, content }) => {
      const div = document.createElement("div");
      div.className = `message ${role}`;
      div.textContent = content;
      return div;
    }),
  );
  messages.scrollTop = messages.scrollHeight;
}

let history = [];

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content) return;
  input.value = "";
  input.disabled = button.disabled = true;
  render([...history, { role: "user", content }, { role: "pending", content: "…" }]);
  try {
    history = (await window.juno.sendChat(chatId, content)).messages;
    render(history);
  } catch (error) {
    // The server stores nothing on failure, so the message is dropped from view.
    render([...history, { role: "error", content: String(error.message || error) }]);
    input.value = content;
  } finally {
    input.disabled = button.disabled = false;
    input.focus();
  }
});
