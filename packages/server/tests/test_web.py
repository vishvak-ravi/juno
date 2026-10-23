"""The phone client's page, served by the API server."""

from fastapi.testclient import TestClient

from juno.config import ServerSettings
from server.app import create_app


def test_the_server_serves_the_phone_client(tmp_path):
    app = create_app(ServerSettings(), data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<title>Juno</title>" in response.text
        # The modules the page imports are served next to it, as are the routes it calls.
        assert client.get("/src/api.js").headers["content-type"].startswith("text/javascript")
        assert client.get("/chats").json()["data"] == []
