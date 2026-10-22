import assert from "node:assert/strict";
import { test } from "node:test";

import { resumeCommand, terminalEnv } from "../src/main/terminal.js";

const conversation = { harness: "claude_code", session_id: "abc", cwd: "/work/project" };

test("a Claude Code conversation is resumed in a new Ghostty window in its directory", () => {
  const ghostty = ["--working-directory=/work/project", "-e", "claude", "--resume", "abc"];
  assert.deepEqual(resumeCommand("linux", conversation), { command: "ghostty", args: ghostty });
  assert.deepEqual(resumeCommand("darwin", conversation), { command: "open", args: ["-na", "Ghostty", "--args", ...ghostty] });
});

test("a conversation of another assistant, or on another platform, is not reopened", () => {
  assert.equal(resumeCommand("linux", { ...conversation, harness: "codex" }), null);
  assert.equal(resumeCommand("win32", conversation), null);
  assert.equal(resumeCommand("linux", null), null);
});

test("the terminal does not inherit the session variables of a Claude Code that started this app", () => {
  const env = { PATH: "/bin", CLAUDECODE: "1", CLAUDE_CODE_SESSION_ID: "s", CLAUDE_PID: "1", CLAUDE_EFFORT: "high",
                CLAUDE_CONFIG_DIR: "/c", JUNO_DEVICE_ID: "laptop" };
  assert.deepEqual(terminalEnv(env), { PATH: "/bin", CLAUDE_CONFIG_DIR: "/c", JUNO_DEVICE_ID: "laptop" });
});
