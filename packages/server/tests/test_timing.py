"""Every response says how long the server spent on it, and the server logs it with its size."""

import logging
import re

from fastapi.testclient import TestClient

from juno.config import CaptureConfig, Config
from server.app import create_app

SERVER_TIMING = re.compile(r"^app;dur=(\d+\.\d)$")


def client_for(tmp_path):
    return TestClient(create_app(Config(capture=CaptureConfig(data_dir=tmp_path))))


def test_a_response_carries_the_milliseconds_the_server_spent_on_it(tmp_path):
    with client_for(tmp_path) as client:
        for path in ("/api/status", "/events", "/no-such-route"):
            response = client.get(path)
            match = SERVER_TIMING.match(response.headers["server-timing"])
            assert match, response.headers["server-timing"]
            assert float(match.group(1)) >= 0


def test_the_server_logs_each_request_with_its_status_duration_and_body_size(tmp_path, caplog):
    with client_for(tmp_path) as client, caplog.at_level(logging.INFO, logger="server.timing"):
        response = client.get("/events")
        logged = [r.getMessage() for r in caplog.records if r.name == "server.timing"]
    assert logged == [f"GET /events 200 {response.headers['server-timing'][8:]}ms "
                      f"{len(response.content)}B"]
