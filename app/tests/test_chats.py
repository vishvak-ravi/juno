"""The chat route, against a stand-in for vLLM that speaks the same two endpoints."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from juno.config import CaptureConfig, Config, LlmConfig
from server.app import create_app


class FakeModelServer(ThreadingHTTPServer):
    """Lists one model and answers every completion with "re: <last message>"."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeModelHandler)
        self.requests: list[dict] = []  # bodies of the completion requests, in order

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

    # The model saw the whole conversation each time, with the configured settings.
    assert [r["messages"] for r in model_server.requests] == [
        [question], [question, answer, follow_up]]
    assert model_server.requests[0]["model"] == "fake-model"
    assert model_server.requests[0]["max_tokens"] == 77
    assert model_server.requests[0]["temperature"] == 0.5


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
