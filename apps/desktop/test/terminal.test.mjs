import assert from "node:assert/strict";
import { test } from "node:test";

import { resumeCommand, terminalEnv } from "../src/main/terminal.js";

const conversation = { harness: "claude_code", session_id: "abc", cwd: "/work/project", entrypoint: "cli", host_session_id: null };
const inApp = { ...conversation, entrypoint: "claude-desktop", host_session_id: "local_app-1" };

test("a Claude Code conversation of a terminal is resumed in a new Ghostty window in its directory", () => {
  const ghostty = ["--working-directory=/work/project", "-e", "claude", "--resume", "abc"];
  const missing = "Ghostty is not installed.";
  assert.deepEqual(resumeCommand("linux", conversation), { command: "ghostty", args: ghostty, cwd: "/work/project", missing });
  assert.deepEqual(resumeCommand("darwin", conversation),
                   { command: "open", args: ["-na", "Ghostty", "--args", ...ghostty], cwd: "/work/project", missing });
});

test("a Claude Code conversation of the Claude desktop app is opened by the app's link to its own session id", () => {
  const link = "claude://code/continue?session=local_app-1";
  assert.deepEqual(resumeCommand("linux", inApp), { command: "xdg-open", args: [link], missing: "xdg-open is not installed." });
  assert.deepEqual(resumeCommand("darwin", inApp), { command: "open", args: [link], missing: "open is not installed." });
});

test("a conversation of another assistant, from elsewhere, or on another platform, is not reopened", () => {
  assert.equal(resumeCommand("linux", { ...conversation, harness: "codex" }), null);
  assert.equal(resumeCommand("linux", { ...conversation, entrypoint: "claude-vscode" }), null);
  assert.equal(resumeCommand("win32", conversation), null);
  assert.equal(resumeCommand("win32", inApp), null);
  assert.equal(resumeCommand("linux", { ...inApp, host_session_id: null }), null);
  assert.equal(resumeCommand("linux", null), null);
});

test("the terminal does not inherit the session variables of a Claude Code that started this app", () => {
  const env = { PATH: "/bin", CLAUDECODE: "1", CLAUDE_CODE_SESSION_ID: "s", CLAUDE_PID: "1", CLAUDE_EFFORT: "high",
                CLAUDE_CONFIG_DIR: "/c", JUNO_DEVICE_ID: "laptop" };
  assert.deepEqual(terminalEnv(env), { PATH: "/bin", CLAUDE_CONFIG_DIR: "/c", JUNO_DEVICE_ID: "laptop" });
});
