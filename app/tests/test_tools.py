"""The bash and web_search tools: on their own, over MCP, and from a chat."""

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.mcp
from juno.config import BashConfig, CaptureConfig, Config, LlmConfig, ToolsConfig, WebSearchConfig
from server.app import create_app
from server.search import SearchError
from server.shell import run_bash
from test_chats import model_server  # noqa: F401  (fixture)

needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap is not installed")
UNSANDBOXED = BashConfig(sandbox=False)


def test_a_command_returns_its_output_and_a_failing_one_its_exit_code(tmp_path):
    assert run_bash("echo out; echo err >&2", UNSANDBOXED, tmp_path, 1000) == "out\nerr\n"
    assert run_bash("echo no; exit 3", UNSANDBOXED, tmp_path, 1000) == "no\n[exit code 3]"
    assert run_bash("true", UNSANDBOXED, tmp_path, 1000) == "[no output]"


def test_files_in_the_workspace_stay_between_commands_and_variables_do_not(tmp_path):
    workspace = tmp_path / "workspace"  # created by the first command
    run_bash("echo kept > note; x=1", UNSANDBOXED, workspace, 1000)
    assert run_bash('cat note; echo "x=$x"', UNSANDBOXED, workspace, 1000) == "kept\nx=\n"


def test_a_command_that_runs_too_long_is_killed_with_what_it_started(tmp_path):
    config = BashConfig(sandbox=False, timeout_seconds=0.5)
    started = time.monotonic()
    result = run_bash("echo before; sleep 30 & sleep 30", config, tmp_path, 1000)
    assert time.monotonic() - started < 10
    assert result == "before\n[killed: still running after 0.5 seconds]"


def test_long_output_keeps_its_start_and_end_within_the_limit(tmp_path):
    result = run_bash("seq 1 100000", UNSANDBOXED, tmp_path, 2000)
    assert len(result) <= 2000
    assert result.startswith("1\n2\n") and result.endswith("99999\n100000\n")
    assert "bytes of output cut here" in result


@needs_bwrap
def test_a_sandboxed_command_sees_neither_the_home_directory_nor_the_environment(
        tmp_path, monkeypatch):
    monkeypatch.setenv("JUNO_TEST_SECRET", "hidden")
    workspace = tmp_path / "workspace"
    result = run_bash(f'ls {Path.home()} 2>&1; echo "secret=$JUNO_TEST_SECRET"; '
                      "echo kept > note; touch /usr/juno-test 2>&1; pwd",
                      BashConfig(), workspace, 2000)
    assert "No such file or directory" in result
    assert "secret=\n" in result
    assert "Read-only file system" in result
    assert result.endswith(f"{workspace}\n")
    assert (workspace / "note").read_text() == "kept\n"


def app_client(tmp_path, tools: ToolsConfig, base_url: str = "http://localhost:1") -> TestClient:
    config = Config(capture=CaptureConfig(data_dir=tmp_path), tools=tools,
                    llm=LlmConfig(base_url=base_url))
    # The MCP endpoint only answers requests addressed to the server's own host.
    return TestClient(create_app(config), base_url="http://127.0.0.1:8765")


def mcp_request(client: TestClient, method: str, params: dict) -> dict:
    response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return response.json()["result"]


def test_an_agent_searches_the_web_over_mcp(tmp_path, monkeypatch):
    asked = []

    def search_web(query, max_results):
        asked.append((query, max_results))
        if query == "nothing":
            raise SearchError("the search failed: no results")
        return [{"title": "A page", "url": "https://example.com", "snippet": "About a thing."}]

    monkeypatch.setattr(server.mcp, "search_web", search_web)
    tools = ToolsConfig(web_search=WebSearchConfig(max_results=3))
    with app_client(tmp_path, tools) as client:
        found = mcp_request(client, "tools/call", {
            "name": "web_search", "arguments": {"query": "a thing", "max_results": 50}})
        failed = mcp_request(client, "tools/call", {
            "name": "web_search", "arguments": {"query": "nothing"}})

    assert json.loads(found["content"][0]["text"]) == {"results": [
        {"title": "A page", "url": "https://example.com", "snippet": "About a thing."}]}
    # Never more results than configured, whatever the agent asks for.
    assert asked[0] == ("a thing", 3)
    assert failed["isError"] and "no results" in failed["content"][0]["text"]


def test_tools_turned_off_are_not_offered(tmp_path):
    off = ToolsConfig(bash=BashConfig(enabled=False), web_search=WebSearchConfig(enabled=False))
    with app_client(tmp_path / "on", ToolsConfig()) as client:
        on_names = {tool["name"] for tool in mcp_request(client, "tools/list", {})["tools"]}
    with app_client(tmp_path / "off", off) as client:
        off_names = {tool["name"] for tool in mcp_request(client, "tools/list", {})["tools"]}
    assert on_names - off_names == {"bash", "web_search"}
    assert "list_annotations" in off_names


def test_the_model_can_run_a_command_before_it_answers(tmp_path, model_server):  # noqa: F811
    tools = ToolsConfig(bash=UNSANDBOXED)
    model_server.tool_calls.append({
        "id": "call-1", "type": "function",
        "function": {"name": "bash", "arguments": '{"command": "echo $((6 * 7))"}'}})
    with app_client(tmp_path, tools, model_server.url) as client:
        response = client.put("/chats/abc", json={"messages": [
            {"role": "user", "content": "a question that needs arithmetic"}]})

    assert response.status_code == 200
    result = model_server.requests[1]["messages"][-1]
    assert (result["role"], result["content"]) == ("tool", "42\n")
    assert response.json()["messages"][-1]["content"] == "re: 42\n"
