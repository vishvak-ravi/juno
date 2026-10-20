"""fwd_to_claude against a real tmux server on its own socket and a stand-in for ``claude``."""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from juno.config import CaptureConfig, ClaudeConfig, Config, ToolsConfig
from server.app import create_app
from server.agent.claude import ClaudeError, TmuxDest, forward_to_claude
from server.agent.tools import call_chat_tool

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")

FAKE_CLAUDE = str(Path(__file__).resolve().parent / "fake_claude.py")


@pytest.fixture
def config():
    """Settings for a tmux server of the test's own, not running yet."""
    socket = f"juno-test-{os.getpid()}"
    yield ClaudeConfig(enabled=True, command=FAKE_CLAUDE, tmux_socket=socket, timeout_seconds=20)
    subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)


def tmux_lines(config: ClaudeConfig, *args: str) -> list[str]:
    done = subprocess.run(["tmux", "-L", config.tmux_socket, *args], capture_output=True,
                          text=True, check=True)
    return done.stdout.splitlines()


def forward(config, tmp_path, prompt, model=None, dest=None, result_chars=1000):
    return forward_to_claude(prompt, model, dest or TmuxDest(), config, tmp_path / "results",
                             tmp_path / "cwd", result_chars)


def test_a_prompt_starts_a_tmux_server_and_returns_claudes_reply(config, tmp_path):
    result = forward(config, tmp_path, "what is new; in $HOME?")

    assert result["reply"] == "default: what is new; in $HOME?"
    assert result["tmux_dest"]["name"] == "claude"
    # The server did not exist; now it has the session with Claude's window in it.
    assert tmux_lines(config, "list-windows", "-a", "-F",
                      "#{session_name} #{window_id} #{window_name}") == [
        f"juno {result['tmux_dest']['window']} claude"]


def test_each_new_conversation_gets_a_window_in_the_running_server(config, tmp_path):
    first = forward(config, tmp_path, "one")["tmux_dest"]
    second = forward(config, tmp_path, "two", model="opus", dest=TmuxDest(name="review"))

    assert second["reply"] == "opus: two"
    assert second["tmux_dest"]["name"] == "review"
    assert second["tmux_dest"]["pane"] != first["pane"]
    assert tmux_lines(config, "list-windows", "-a", "-F", "#{window_name}") == ["claude", "review"]
    # The window the user is looking at stays selected.
    assert tmux_lines(config, "display-message", "-p", "#{window_id}") == [first["window"]]


def test_a_prompt_to_a_returned_pane_continues_that_conversation(config, tmp_path):
    first = forward(config, tmp_path, "one", model="sonnet")
    again = forward(config, tmp_path, "and then?", dest=TmuxDest(pane=first["tmux_dest"]["pane"]))

    # Answered by the process the first call started, with that call's model.
    assert again["reply"] == "sonnet: and then?"
    assert again["tmux_dest"] == first["tmux_dest"]
    assert len(tmux_lines(config, "list-panes", "-a")) == 1


def test_a_window_id_puts_the_new_conversation_in_a_pane_of_that_window(config, tmp_path):
    first = forward(config, tmp_path, "one")["tmux_dest"]
    second = forward(config, tmp_path, "two", dest=TmuxDest(window=first["window"]))["tmux_dest"]

    assert second["window"] == first["window"] and second["pane"] != first["pane"]


def test_a_reply_longer_than_the_limit_is_cut(config, tmp_path):
    result = forward(config, tmp_path, "x" * 500, result_chars=100)
    assert result["reply"].startswith("default: xxx")
    assert "more characters not shown" in result["reply"]
    assert len(result["reply"]) < 200


def test_when_claude_does_not_finish_the_error_shows_its_pane(config, tmp_path):
    slow = config.model_copy(update={"timeout_seconds": 1.5})
    with pytest.raises(ClaudeError) as raised:
        forward(slow, tmp_path, "hang on this")
    assert "has not finished after 1.5 seconds" in str(raised.value)
    assert "Do you want to proceed?" in str(raised.value)


def test_errors_name_what_went_wrong(config, tmp_path):
    with pytest.raises(ClaudeError, match="closed before Claude replied"):
        forward(config, tmp_path, "exit now")
    with pytest.raises(ClaudeError, match="no tmux pane %999"):
        forward(config, tmp_path, "hello", dest=TmuxDest(pane="%999"))
    with pytest.raises(ClaudeError, match="empty"):
        forward(config, tmp_path, "  ")


def test_the_model_forwards_a_prompt_only_when_enabled(config, tmp_path):
    def call(tools: ToolsConfig) -> str:
        app = create_app(Config(capture=CaptureConfig(data_dir=tmp_path), tools=tools))
        arguments = json.dumps({"prompt": "one", "tmux_dest": {"name": "from-chat"}})
        with TestClient(app) as client:
            return asyncio.run(call_chat_tool(client.app.state.chat_tools, "fwd_to_claude",
                                              arguments))

    result = json.loads(call(ToolsConfig(claude=config)))
    assert result["reply"] == "default: one"
    assert result["tmux_dest"]["name"] == "from-chat"
    assert call(ToolsConfig(claude=ClaudeConfig(enabled=False))).startswith("error:")
