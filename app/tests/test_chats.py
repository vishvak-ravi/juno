import json

from fastapi.testclient import TestClient

from server.app import create_app


def test_puts_to_the_same_id_append_to_the_conversation(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    question = {"role": "user", "content": "first question"}
    answer = {"role": "assistant", "content": "first answer"}
    follow_up = {"role": "user", "content": "second question"}

    first = client.put("/chats/abc", json={"messages": [question, answer]})
    assert first.status_code == 200
    assert first.json() == {"id": "abc", "messages": [question, answer]}

    second = client.put("/chats/abc", json={"messages": [follow_up]})
    assert second.status_code == 200
    conversation = {"id": "abc", "messages": [question, answer, follow_up]}
    assert second.json() == conversation

    assert client.get("/chats/abc").json() == conversation
    assert json.loads((tmp_path / "chats" / "abc.json").read_text()) == conversation
