// What reopens an assistant conversation that a moment carries (`conversation`
// in GET /events): for Claude Code, `claude --resume <session_id>` in a new
// Ghostty window opened in the conversation's directory. No Electron here, so
// the tests can load it.

// The command and arguments for `platform` (process.platform), or null when
// the conversation is not one this computer can reopen.
function resumeCommand(platform, conversation) {
  if (conversation?.harness !== "claude_code") return null;
  const ghostty = [`--working-directory=${conversation.cwd}`, "-e", "claude", "--resume", conversation.session_id];
  if (platform === "linux") return { command: "ghostty", args: ghostty };
  // On macOS Ghostty's own binary does not open windows; open -n starts another instance of the app.
  if (platform === "darwin") return { command: "open", args: ["-na", "Ghostty", "--args", ...ghostty] };
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
