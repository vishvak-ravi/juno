"""The chat routes, against a stand-in for vLLM that speaks the same two endpoints."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from anyio import to_thread

from juno.config import CaptureConfig, Config, LlmConfig
from server.app import create_app
from server.routes import chats


class FakeModelServer(ThreadingHTTPServer):
    """Lists one model and answers every completion with "re: <last message>".

    While ``tool_calls`` is not empty, a completion is answered with the first
    of them instead.
    """

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeModelHandler)
        self.requests: list[dict] = []  # bodies of the completion requests, in order
        self.tool_calls: list[dict] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class FakeModelHandler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def reply(self, body: dict):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.path == "/v1/models"
        self.reply({"data": [{"id": "fake-model"}]})

    def do_POST(self):
        assert self.path == "/v1/chat/completions"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        if self.server.tool_calls:
            call = self.server.tool_calls.pop(0)
            self.reply({"choices": [{"message": {"role": "assistant", "content": None,
                                                 "tool_calls": [call]},
                                     "finish_reason": "tool_calls"}]})
            return
        content = "re: " + body["messages"][-1]["content"]
        self.reply({"choices": [{"message": {"role": "assistant", "content": content},
                                 "finish_reason": "stop"}]})


@pytest.fixture
def model_server():
    server = FakeModelServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def client_for(tmp_path, base_url: str) -> TestClient:
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    llm=LlmConfig(base_url=base_url, max_tokens=77, temperature=0.5))
    return TestClient(create_app(config))


def test_puts_to_the_same_id_append_the_messages_and_the_replies(tmp_path, model_server):
    client = client_for(tmp_path, model_server.url)
    question = {"role": "user", "content": "first question"}
    follow_up = {"role": "user", "content": "second question"}
    answer = {"role": "assistant", "content": "re: first question"}
    second_answer = {"role": "assistant", "content": "re: second question"}

    first = client.put("/chats/abc", json={"messages": [question]})
    assert first.status_code == 200
    assert first.json() == {"id": "abc", "messages": [question, answer]}

    second = client.put("/chats/abc", json={"messages": [follow_up]})
    assert second.status_code == 200
    conversation = {"id": "abc", "messages": [question, answer, follow_up, second_answer]}
    assert second.json() == conversation

    assert client.get("/chats/abc").json() == conversation
    assert json.loads((tmp_path / "chats" / "abc.json").read_text()) == conversation

    # The model saw the whole conversation each time, after a system prompt
    # that is not stored, with the configured settings.
    assert [r["messages"][0]["role"] for r in model_server.requests] == ["system", "system"]
    assert [r["messages"][1:] for r in model_server.requests] == [
        [question], [question, answer, follow_up]]
    assert model_server.requests[0]["model"] == "fake-model"
    assert model_server.requests[0]["max_tokens"] == 77
    assert model_server.requests[0]["temperature"] == 0.5


def test_the_model_can_read_the_record_before_it_answers(tmp_path, model_server):
    client = client_for(tmp_path, model_server.url)
    question = {"role": "user", "content": "a question about recent activity"}
    model_server.tool_calls.append({
        "id": "call-1", "type": "function",
        "function": {"name": "find_events", "arguments": '{"last_minutes": 60}'}})

    response = client.put("/chats/abc", json={"messages": [question]})
    assert response.status_code == 200

    # The model was offered the tools, and asked again with the result of its call.
    first, second = model_server.requests
    assert "find_events" in [tool["function"]["name"] for tool in first["tools"]]
    result = second["messages"][-1]
    assert (result["role"], result["tool_call_id"]) == ("tool", "call-1")
    assert json.loads(result["content"])["runs"] == []
    # Only the question and the final answer are kept.
    assert response.json()["messages"] == [
        question, {"role": "assistant", "content": "re: " + result["content"]}]


def test_activity_names_the_tool_a_reply_is_waiting_for(tmp_path, model_server, monkeypatch):
    client = client_for(tmp_path, model_server.url)
    question = {"role": "user", "content": "a question about recent activity"}
    model_server.tool_calls.append({
        "id": "call-1", "type": "function",
        "function": {"name": "find_events", "arguments": "{}"}})
    idle = {"replying": False, "tool": None, "messages": []}
    assert client.get("/chats/abc/activity").json() == idle

    # The tool does not return until the test has looked at the activity.
    called, finish = threading.Event(), threading.Event()

    async def slow_tool(mcp, name, arguments):
        called.set()
        await to_thread.run_sync(finish.wait)
        return "nothing"

    monkeypatch.setattr(chats, "call_chat_tool", slow_tool)
    responses = []
    put = threading.Thread(target=lambda: responses.append(
        client.put("/chats/abc", json={"messages": [question]})))
    put.start()
    try:
        assert called.wait(timeout=10)
        assert client.get("/chats/abc/activity").json() == {
            "replying": True, "tool": "find_events", "messages": [question]}
        # The question is stored only with its reply, and other chats are not waiting.
        assert client.get("/chats/abc").status_code == 404
        assert client.get("/chats/other/activity").json() == idle
    finally:
        finish.set()
        put.join(timeout=10)

    assert responses[0].status_code == 200
    assert client.get("/chats/abc/activity").json() == idle


def test_activity_ends_when_the_reply_fails(tmp_path):
    client = client_for(tmp_path, "http://127.0.0.1:1")
    question = {"role": "user", "content": "first question"}
    assert client.put("/chats/abc", json={"messages": [question]}).status_code == 502
    assert client.get("/chats/abc/activity").json() == {
        "replying": False, "tool": None, "messages": []}


def test_a_chat_with_its_own_system_message_keeps_it(tmp_path, model_server):
    client = client_for(tmp_path, model_server.url)
    messages = [{"role": "system", "content": "be brief"},
                {"role": "user", "content": "first question"}]
    assert client.put("/chats/abc", json={"messages": messages}).status_code == 200
    assert model_server.requests[0]["messages"] == messages


def test_nothing_is_stored_when_the_model_is_unreachable(tmp_path, model_server):
    client = client_for(tmp_path, model_server.url)
    question = {"role": "user", "content": "first question"}
    assert client.put("/chats/abc", json={"messages": [question]}).status_code == 200
    model_server.shutdown()
    model_server.server_close()

    response = client.put("/chats/abc", json={"messages": [{"role": "user", "content": "more"}]})
    assert response.status_code == 502
    assert "could not reach" in response.json()["detail"]
    assert client.get("/chats/abc").json()["messages"] == [
        question, {"role": "assistant", "content": "re: first question"}]


def store_chat(tmp_path, chat_id: str, messages: list[dict], updated_at: int):
    path = tmp_path / "chats" / f"{chat_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"id": chat_id, "messages": messages}))
    os.utime(path, (updated_at, updated_at))


def test_list_is_empty_before_any_chat(tmp_path):
    client = client_for(tmp_path, "http://unused")
    assert client.get("/chats").json() == {
        "object": "list", "data": [], "first_id": None, "last_id": None, "has_more": False}


def test_list_summarizes_chats_newest_change_first(tmp_path):
    store_chat(tmp_path, "old", [{"role": "system", "content": "be brief"},
                                 {"role": "user", "content": "first line\nsecond line"},
                                 {"role": "assistant", "content": "ok"}], 1000)
    store_chat(tmp_path, "new", [], 2000)
    (tmp_path / "chats" / "broken.json").write_text("not json")
    client = client_for(tmp_path, "http://unused")

    body = client.get("/chats").json()
    assert body["data"] == [
        {"id": "new", "title": None, "message_count": 0, "updated_at": 2000},
        {"id": "old", "title": "first line", "message_count": 3, "updated_at": 1000},
    ]
    assert (body["first_id"], body["last_id"], body["has_more"]) == ("new", "old", False)
    assert [c["id"] for c in client.get("/chats?order=asc").json()["data"]] == ["old", "new"]


def test_list_pages_with_the_after_cursor(tmp_path):
    for n in range(5):
        store_chat(tmp_path, f"c{n}", [], 1000 + n)
    client = client_for(tmp_path, "http://unused")

    first = client.get("/chats?limit=2").json()
    assert [c["id"] for c in first["data"]] == ["c4", "c3"]
    assert first["has_more"] is True
    second = client.get(f"/chats?limit=2&after={first['last_id']}").json()
    assert [c["id"] for c in second["data"]] == ["c2", "c1"]
    last = client.get(f"/chats?limit=2&after={second['last_id']}").json()
    assert [c["id"] for c in last["data"]] == ["c0"]
    assert last["has_more"] is False

    assert client.get("/chats?after=missing").status_code == 400
    assert client.get("/chats?limit=0").status_code == 422
