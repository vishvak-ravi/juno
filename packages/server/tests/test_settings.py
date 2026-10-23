"""``/settings``: what a client shows of the server's settings, and changes to them."""

from fastapi.testclient import TestClient

from juno.config import ServerSettings
from server.app import create_app


def client_for(tmp_path) -> TestClient:
    """The server as started on ``tmp_path``: with the settings saved there."""
    return TestClient(create_app(data_dir=tmp_path, annotate_request=lambda request: None))


def fields(view) -> dict:
    return {f["path"]: f for section in view["sections"] for f in section["fields"]}


def test_every_setting_is_listed_with_its_value_default_and_description(tmp_path):
    view = client_for(tmp_path).get("/settings").json()
    model = fields(view)["annotate.model"]
    assert model["value"] == model["default"] == ServerSettings().annotate.model
    assert model["kind"] == "string" and model["description"]
    assert fields(view)["annotate.chunk_size"]["advanced"]
    assert view["restart"] == []


def test_a_change_is_saved_and_applies_when_the_server_restarts(tmp_path):
    client = client_for(tmp_path)
    view = client.patch("/settings", json={"set": {"annotate.model": "openai/local",
                                                  "annotate.api_base": "http://localhost:9/v1"}}).json()
    assert fields(view)["annotate.model"]["value"] == "openai/local"
    assert sorted(view["restart"]) == ["annotate.api_base", "annotate.model"]
    assert client.get("/settings").json()["restart"] == view["restart"]

    restarted = client_for(tmp_path).get("/settings").json()
    assert fields(restarted)["annotate.model"]["value"] == "openai/local"
    assert restarted["restart"] == []


def test_putting_a_setting_back_to_its_default_clears_the_restart(tmp_path):
    client = client_for(tmp_path)
    client.patch("/settings", json={"set": {"keypoints.moment_on": 0.9}})
    view = client.patch("/settings", json={"reset": ["keypoints.moment_on"]}).json()
    assert fields(view)["keypoints.moment_on"]["value"] == ServerSettings().keypoints.moment_on
    assert view["restart"] == []


def test_a_refused_change_names_each_setting_and_saves_nothing(tmp_path):
    client = client_for(tmp_path)
    response = client.patch("/settings", json={"set": {"keypoints.moment_on": "high", "annotate.modle": "x"}})
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"annotate.modle"}
    response = client.patch("/settings", json={"set": {"keypoints.moment_on": "high"}})
    assert response.status_code == 422 and "keypoints.moment_on" in response.json()["detail"]
    assert client.get("/settings").json()["restart"] == []


def test_an_api_key_is_shown_as_set_never_read_back(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key-4f1c")
    env = {v["name"]: v for v in client_for(tmp_path).get("/settings").json()["environment"]}
    assert env["TYPESAFE_API_KEY"]["set"] and env["TYPESAFE_API_KEY"]["value"] is None
    assert "ts-key-4f1c" not in client_for(tmp_path).get("/settings").text
