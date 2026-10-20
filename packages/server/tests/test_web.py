"""The phone client's page, served by the API server."""

from fastapi.testclient import TestClient

from juno.config import CaptureConfig, Config
from server.app import create_app


def test_the_server_serves_the_phone_client(tmp_path):
    app = create_app(Config(capture=CaptureConfig(data_dir=tmp_path)))
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<title>Juno</title>" in response.text
        # The routes the page calls still answer next to it.
        assert client.get("/chats").json()["data"] == []
