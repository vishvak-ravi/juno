"""Prompts forwarded to Claude Code running in a tmux pane, where the user can watch it.

A call either starts ``claude`` in a new pane with the prompt as its first
message, or pastes the prompt into a pane an earlier call started. Either way
it then waits for Claude to finish its turn and returns the last message.

The end of a turn is reported by Claude Code itself: every ``claude`` started
here gets a Stop hook (through ``--settings``) that writes the hook's input,
which carries ``last_assistant_message``, to ``<results_dir>/<pane id>.json``.
The call deletes that file before it sends the prompt and waits for it to
come back.

Claude runs as the server's user, outside the sandbox bash commands run in.
"""

import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from juno.config import ClaudeConfig

# Seconds between two looks for Claude's reply.
POLL_SECONDS = 0.5
# Seconds between pasting a prompt into a pane and pressing Enter, so that the
# paste has been taken in before the key arrives.
PASTE_SETTLE_SECONDS = 0.5
# Lines from the bottom of the pane shown when Claude does not reply in time.
SCREEN_LINES = 30
BUFFER_NAME = "juno-fwd-to-claude"
DEST_FORMAT = "#{window_id} #{pane_id} #{window_name}"


class ClaudeError(Exception):
    """The prompt could not be delivered, or Claude did not reply."""


class TmuxDest(BaseModel):
    """Where in tmux a Claude Code conversation runs."""

    window: Optional[str] = Field(
        default=None, pattern=r"^@\d+$",
        description='Id of an existing tmux window, like "@3": Claude is started in a '
                    "new pane of that window. Left out: a new window.")
    pane: Optional[str] = Field(
        default=None, pattern=r"^%\d+$",
        description='Id of a pane an earlier call returned, like "%12": the prompt '
                    "continues the conversation in that pane instead of starting one.")
    name: Optional[str] = Field(
        default=None, max_length=40,
        description='Name for the new window. Left out: "claude".')


def tmux(config: ClaudeConfig, *args: str, stdin: Optional[str] = None) -> str:
    """Run one tmux command against the configured server and return its output."""
    socket = ["-L", config.tmux_socket] if config.tmux_socket else []
    try:
        done = subprocess.run(["tmux", *socket, *args], input=stdin, capture_output=True,
                              text=True)
    except FileNotFoundError:
        raise ClaudeError("tmux is not installed on the Juno server's machine")
    if done.returncode != 0:
        raise ClaudeError(f"tmux {args[0]} failed: {done.stderr.strip()}")
    return done.stdout


def server_running(config: ClaudeConfig) -> bool:
    try:
        tmux(config, "list-sessions")
    except ClaudeError:
        return shutil.which("tmux") is None  # no tmux: let the next call report it
    return True


def pane_exists(config: ClaudeConfig, pane: str) -> bool:
    try:
        return pane in tmux(config, "list-panes", "-a", "-F", "#{pane_id}").split()
    except ClaudeError:
        return False


def hook_settings(results_dir: Path) -> str:
    """Claude Code settings with the Stop hook that reports the end of each turn."""
    result = f'{shlex.quote(str(results_dir))}/"$TMUX_PANE".json'
    # Written under another name first: the reader must never see half a file.
    command = f"cat > {result}.tmp && mv {result}.tmp {result}"
    return json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}})


def claude_argv(config: ClaudeConfig, model: Optional[str], prompt: str,
                results_dir: Path) -> list[str]:
    """The command line that starts Claude Code with ``prompt`` as its first message."""
    argv = [config.command, "--settings", hook_settings(results_dir)]
    if model:
        argv += ["--model", model]
    if config.permission_mode:
        argv += ["--permission-mode", config.permission_mode]
    return argv + ["--", prompt]


def start(config: ClaudeConfig, dest: TmuxDest, argv: list[str], cwd: Path) -> TmuxDest:
    """Run ``argv`` in a new pane and return where it is.

    The pane goes into ``dest.window`` if given, else into a new window of the
    session tmux considers current, else into a new server's first window.
    The user's own window and pane stay selected.
    """
    name = dest.name or config.window_name
    options = ["-d", "-P", "-F", DEST_FORMAT, "-c", str(cwd)]
    if dest.window:
        made = tmux(config, "split-window", *options, "-t", dest.window, "--", *argv)
    elif server_running(config):
        made = tmux(config, "new-window", *options, "-n", name, "--", *argv)
    else:
        made = tmux(config, "new-session", *options, "-s", config.tmux_session, "-n", name,
                    "--", *argv)
    window, pane, window_name = made.strip().split(" ", 2)
    return TmuxDest(window=window, pane=pane, name=window_name)


def send(config: ClaudeConfig, pane: str, prompt: str) -> TmuxDest:
    """Paste ``prompt`` into the Claude Code running in ``pane`` and submit it."""
    if not pane_exists(config, pane):
        raise ClaudeError(f"there is no tmux pane {pane}; leave tmux_dest out to start "
                          "a new Claude conversation")
    tmux(config, "load-buffer", "-b", BUFFER_NAME, "-", stdin=prompt)
    # -p: as a bracketed paste, so that a newline in the prompt does not submit it.
    tmux(config, "paste-buffer", "-d", "-p", "-b", BUFFER_NAME, "-t", pane)
    time.sleep(PASTE_SETTLE_SECONDS)
    tmux(config, "send-keys", "-t", pane, "Enter")
    window, window_name = tmux(config, "display-message", "-p", "-t", pane,
                               "#{window_id} #{window_name}").strip().split(" ", 1)
    return TmuxDest(window=window, pane=pane, name=window_name)


def screen(config: ClaudeConfig, pane: str) -> str:
    """The last lines the pane shows."""
    lines = tmux(config, "capture-pane", "-p", "-t", pane).rstrip().splitlines()
    return "\n".join(lines[-SCREEN_LINES:])


def wait_for_reply(config: ClaudeConfig, pane: str, result_file: Path) -> str:
    """Claude's last message of the turn, once the Stop hook has written it."""
    deadline = time.monotonic() + config.timeout_seconds
    while time.monotonic() < deadline:
        try:
            return json.loads(result_file.read_text()).get("last_assistant_message") or ""
        except FileNotFoundError:
            pass
        if not pane_exists(config, pane):
            raise ClaudeError(f"the pane {pane} closed before Claude replied; `{config.command}` "
                              "may have failed to start (check the model name)")
        time.sleep(POLL_SECONDS)
    raise ClaudeError(
        f"Claude has not finished after {config.timeout_seconds:g} seconds. It is still "
        f"running in tmux pane {pane} and may be waiting for the user to approve "
        f"something there. The pane shows:\n{screen(config, pane)}")


def forward_to_claude(prompt: str, model: Optional[str], dest: TmuxDest, config: ClaudeConfig,
                      results_dir: Path, cwd: Path, result_chars: int) -> dict:
    """Deliver ``prompt`` to Claude Code and return its reply and where it runs.

    Args:
        model: the model of a new conversation; None for ``config.model``, and
            if that is None too, Claude Code's own default.
        dest: see ``TmuxDest``; all None starts a conversation in a new window.
        results_dir: where the Stop hooks write; created if missing.
        cwd: the directory a new Claude Code starts in; created if missing.
        result_chars: the most characters of the reply to return.
    """
    if not prompt.strip():
        raise ClaudeError("the prompt is empty")
    model = model or config.model
    if model and model.startswith("-"):
        raise ClaudeError(f"{model} is not a model name")
    results_dir.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    if dest.pane:
        (results_dir / f"{dest.pane}.json").unlink(missing_ok=True)
        where = send(config, dest.pane, prompt)
    else:
        # A new pane has a new id, so no result of an earlier turn can be there.
        where = start(config, dest, claude_argv(config, model, prompt, results_dir), cwd)
    reply = wait_for_reply(config, where.pane, results_dir / f"{where.pane}.json")
    if len(reply) > result_chars:
        reply = reply[:result_chars] + (f"\n[cut: {len(reply) - result_chars} more characters "
                                        "not shown; ask Claude for a shorter answer]")
    return {"reply": reply, "tmux_dest": where.model_dump()}
