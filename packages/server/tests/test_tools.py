"""The chat model's tools: bash, web_search and the past chats, and the session tools agents
get over MCP."""

import asyncio
import base64
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.agent.tools
from annotate.store import read_caption_file, write_caption_file
from juno.config import (AnnotateConfig, BashConfig, ClaudeConfig, KeypointsConfig, LlmConfig,
                         ServerSettings, ToolsConfig, WebSearchConfig)
from server.app import create_app
from server.agent.search import SearchError
from server.agent.shell import run_bash
from server.agent.tools import call_chat_tool
from test_chats import model_server, store_chat  # noqa: F401  (model_server is a fixture)
from test_keypoints import SESSION, FakeJev, wait_for_answers, write_session

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
    config = ServerSettings(tools=tools,
                            llm=LlmConfig(base_url=base_url))
    # The MCP endpoint only answers requests addressed to the server's own host.
    return TestClient(create_app(config, data_dir=tmp_path), base_url="http://127.0.0.1:8765")


def mcp_request(client: TestClient, method: str, params: dict) -> dict:
    response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return response.json()["result"]


def call(client: TestClient, name: str, arguments: dict) -> str:
    """What the chat model is shown for a call it made."""
    return asyncio.run(call_chat_tool(client.app.state.chat_tools, name, json.dumps(arguments)))


def test_the_model_searches_the_web(tmp_path, monkeypatch):
    asked = []

    def search_web(query, max_results):
        asked.append((query, max_results))
        if query == "nothing":
            raise SearchError("the search failed: no results")
        return [{"title": "A page", "url": "https://example.com", "snippet": "About a thing."}]

    monkeypatch.setattr(server.agent.tools, "search_web", search_web)
    tools = ToolsConfig(web_search=WebSearchConfig(max_results=3))
    with app_client(tmp_path, tools) as client:
        found = call(client, "web_search", {"query": "a thing", "max_results": 50})
        failed = call(client, "web_search", {"query": "nothing"})

    assert json.loads(found) == {"results": [
        {"title": "A page", "url": "https://example.com", "snippet": "About a thing."}]}
    # Never more results than configured, whatever the agent asks for.
    assert asked[0] == ("a thing", 3)
    assert failed.startswith("error:") and "no results" in failed


def test_tools_turned_off_are_not_offered(tmp_path):
    off = ToolsConfig(bash=BashConfig(enabled=False), web_search=WebSearchConfig(enabled=False))
    with app_client(tmp_path / "on", ToolsConfig()) as client:
        on_names = set(client.app.state.chat_tools)
    with app_client(tmp_path / "off", off) as client:
        off_names = set(client.app.state.chat_tools)
    assert on_names - off_names == {"bash", "web_search"}
    assert {"find_events", "search_chats"} <= off_names


def test_agents_get_only_the_session_tools_over_mcp(tmp_path):
    every = ToolsConfig(claude=ClaudeConfig(enabled=True))
    with app_client(tmp_path, every) as client:
        names = {tool["name"] for tool in mcp_request(client, "tools/list", {})["tools"]}
        refused = mcp_request(client, "tools/call", {"name": "bash",
                                                     "arguments": {"command": "true"}})
    assert names == {"list_sessions", "find_events", "get_event"}
    assert refused["isError"]


def tool_text(client: TestClient, name: str, arguments: dict):
    """What an agent is shown over MCP: the JSON of the first block, or the error."""
    result = mcp_request(client, "tools/call", {"name": name, "arguments": arguments})
    text = result["content"][0]["text"]
    return text if result["isError"] else json.loads(text)


def test_an_agent_finds_what_the_user_saw_and_opens_it_with_its_screenshot(tmp_path):
    # Three rows, a second apart, all captioned "Typed."; Jev sees an error and every action.
    directory = write_session(tmp_path, [["key_press"], [], ["mouse_down"]])
    caption = read_caption_file(directory, 1)
    write_caption_file(directory, caption.model_copy(update={"annotation": caption.annotation.model_copy(
        update={"dense_caption": "~/work $ python -m server"})}))
    config = ServerSettings(annotate=AnnotateConfig(chunk_size=2, scan_interval_seconds=0.05),
                            keypoints=KeypointsConfig(moment_on=0.7, moment_off=0.4))
    app = create_app(config, data_dir=tmp_path, annotate_request=lambda request: None, ask=FakeJev())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        wait_for_answers(tmp_path, 2)
        sessions = tool_text(client, "list_sessions", {})
        found = tool_text(client, "find_events", {"query": "Python server"})
        nothing = tool_text(client, "find_events", {"query": "submarine"})
        landed = tool_text(client, "find_events", {"moment": "code_landed"})
        bad_moment = tool_text(client, "find_events", {"moment": "lunch"})
        newest = mcp_request(client, "tools/call",
                             {"name": "get_event", "arguments": {"device": "laptop"}})
        missing = tool_text(client, "get_event", {"event_id": "nope"})
        chat_view = call(client, "get_event", {"event_id": f"{SESSION}-000000"})

    [session] = sessions["sessions"]
    assert (session["id"], session["device"], session["events"]) == (SESSION, "laptop", 3)
    # An action moment on each of the two rows with input; the error stays on screen: one.
    assert session["moments"]["code_landed"] == 2 and session["moments"]["error_shown"] == 1
    assert session["indices"] == {"sensitivity": 0.0, "agent": 0.1, "error": 0.9}

    # One caption across the session: one run, found by the text on screen of its second chunk.
    assert [(run["first_event"], run["events"]) for run in found["runs"]] == [
        (f"{SESSION}-000000", 3)]
    assert "python -m server" in found["runs"][0]["snippet"]
    assert nothing["runs"] == []
    assert [run["first_event"] for run in landed["runs"]] == [f"{SESSION}-000000"]
    assert "code_landed" in bad_moment

    event = json.loads(newest["content"][0]["text"])
    assert (event["id"], event["caption"]) == (f"{SESSION}-000002", "Typed.")
    assert event["text_on_screen"] == "~/work $ python -m server"
    assert "code_landed" in event["moments"]
    image = newest["content"][1]
    assert image["type"] == "image" and image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == (directory / "2.png").read_bytes()
    assert "first_event from find_events" in missing
    # Juno's own chat model gets the text without the image.
    assert json.loads(chat_view.rsplit("\n", 1)[0])["id"] == f"{SESSION}-000000"
    assert chat_view.endswith("[an image you are not shown]")


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


def test_the_model_finds_a_past_chat_and_reads_it(tmp_path):
    store_chat(tmp_path, "garden", [
        {"role": "system", "content": "be brief about tomatoes"},
        {"role": "user", "content": "when should the tomatoes be planted"},
        {"role": "assistant", "content": "Plant tomatoes after the last frost."}], 1000)
    store_chat(tmp_path, "travel", [
        {"role": "user", "content": "which train goes to the coast"},
        {"role": "assistant", "content": "The morning train."}], 2000)
    (tmp_path / "chats" / "broken.json").write_text("not json")

    with app_client(tmp_path, ToolsConfig()) as client:
        found = json.loads(call(client, "search_chats", {"query": "Tomatoes frost"}))
        nothing = json.loads(call(client, "search_chats", {"query": "submarine"}))
        one = json.loads(call(client, "search_chats", {"query": "train", "limit": 1}))
        chat = json.loads(call(client, "get_chat", {"chat_id": "garden"}))
        rest = json.loads(call(client, "get_chat", {"chat_id": "garden", "first_message": 3}))
        missing = call(client, "get_chat", {"chat_id": "nowhere"})
        outside = call(client, "get_chat", {"chat_id": "../chats/garden"})

    # The message with both words comes first; system messages are not searched.
    assert [(m["chat_id"], m["message"], m["role"]) for m in found["matches"]] == [
        ("garden", 3, "assistant"), ("garden", 2, "user")]
    assert found["matches"][0]["title"] == "when should the tomatoes be planted"
    assert found["matches"][0]["snippet"] == "Plant tomatoes after the last frost."
    assert nothing == {"matches": []}
    assert len(one["matches"]) == 1 and one["matches"][0]["chat_id"] == "travel"
    assert chat == {"chat_id": "garden", "title": "when should the tomatoes be planted",
                    "more_available": False, "messages": [
                        {"message": 2, "role": "user",
                         "content": "when should the tomatoes be planted"},
                        {"message": 3, "role": "assistant",
                         "content": "Plant tomatoes after the last frost."}]}
    assert [m["message"] for m in rest["messages"]] == [3]
    assert missing.startswith("error:") and "no chat with id nowhere" in missing
    assert outside.startswith("error:")


def test_past_chat_results_stay_within_the_result_limit(tmp_path):
    long = "filler " * 300 + "lighthouse " + "filler " * 300
    store_chat(tmp_path, "long", [{"role": "user", "content": long},
                                  {"role": "assistant", "content": long},
                                  {"role": "user", "content": "the lighthouse again"}], 1000)
    config = ServerSettings(llm=LlmConfig(base_url="http://localhost:1", tool_result_chars=1500))
    with TestClient(create_app(config, data_dir=tmp_path), base_url="http://127.0.0.1:8765") as client:
        found = call(client, "search_chats", {"query": "lighthouse"})
        first = call(client, "get_chat", {"chat_id": "long"})
        last = call(client, "get_chat", {"chat_id": "long", "first_message": 3})

    assert len(found) <= 1500 and len(first) <= 1500
    # A snippet shows the part of a long message around the word found.
    snippet = json.loads(found)["matches"][-1]["snippet"]
    assert "lighthouse" in snippet and snippet.startswith("…") and snippet.endswith("…")
    # The first message alone is too long: it is cut, and the rest is left for another call.
    first = json.loads(first)
    assert first["more_available"] is True
    assert [m["message"] for m in first["messages"]] == [1]
    assert first["messages"][0]["content"].endswith(" [cut]")
    assert json.loads(last)["more_available"] is False
