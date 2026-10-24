// What reopens an assistant conversation that a moment carries (`conversation`
// in GET /events) where it ran (`entrypoint`): Claude Code in a terminal (`cli`)
// is resumed by `claude --resume <session_id>` in a new Ghostty window opened in
// the conversation's directory; Claude Code in the Claude desktop app
// (`claude-desktop`) is opened by the app's `claude://code/continue` link to the
// session id the app gave it (`host_session_id`). No Electron here, so the
// tests can load it.

// The command for `platform` (process.platform): `{ command, args, cwd, missing }`,
// `cwd` the directory the command runs in on this computer (none for a link) and
// `missing` what to say when the command is not installed; null when the
// conversation is not one this computer can reopen.
function resumeCommand(platform, conversation) {
  if (conversation?.harness !== "claude_code") return null;
  if (conversation.entrypoint === "claude-desktop") {
    if (!conversation.host_session_id) return null;
    const link = `claude://code/continue?session=${encodeURIComponent(conversation.host_session_id)}`;
    if (platform === "linux") return { command: "xdg-open", args: [link], missing: "xdg-open is not installed." };
    if (platform === "darwin") return { command: "open", args: [link], missing: "open is not installed." };
    return null;
  }
  if (conversation.entrypoint !== "cli") return null;
  const ghostty = [`--working-directory=${conversation.cwd}`, "-e", "claude", "--resume", conversation.session_id];
  const missing = "Ghostty is not installed.";
  if (platform === "linux") return { command: "ghostty", args: ghostty, cwd: conversation.cwd, missing };
  // On macOS Ghostty's own binary does not open windows; open -n starts another instance of the app.
  if (platform === "darwin") return { command: "open", args: ["-na", "Ghostty", "--args", ...ghostty], cwd: conversation.cwd, missing };
  return null;
}

// `env` without the variables a running Claude Code sets for the processes it
// starts (CLAUDECODE, CLAUDE_PID, CLAUDE_EFFORT, CLAUDE_CODE_*): when this app
// was started from a shell Claude Code opened, a conversation resumed with
// them would take that session's id for its own. CLAUDE_CONFIG_DIR is kept.
const SESSION_VARIABLE = /^(CLAUDECODE|CLAUDE_PID|CLAUDE_EFFORT|CLAUDE_CODE_.*)$/;
function terminalEnv(env) {
  return Object.fromEntries(Object.entries(env).filter(([name]) => !SESSION_VARIABLE.test(name)));
}

module.exports = { resumeCommand, terminalEnv };
