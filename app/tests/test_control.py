"""The control API: switches clients set, and what recorders report and are told."""

from fastapi.testclient import TestClient

from juno.config import CaptureConfig, Config
from server.app import create_app


def client_for(tmp_path) -> TestClient:
    return TestClient(create_app(Config(capture=CaptureConfig(data_dir=tmp_path)),
                                 annotate_request=lambda request: None))


def report(client, device, capture="running", session_id=None):
    response = client.put(f"/control/devices/{device}/report",
                          json={"capture": capture, "session_id": session_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_device_appears_recording_when_it_first_reports(tmp_path):
    client = client_for(tmp_path)
    assert client.get("/control").json()["devices"] == {}

    assert report(client, "laptop", session_id="laptop-session_20260101_120000") == {"capture": True}

    device = client.get("/control").json()["devices"]["laptop"]
    assert device["capture"] is True
    assert device["online"] is True
    assert device["reported"] == {"capture": "running",
                                  "session_id": "laptop-session_20260101_120000"}


def test_turning_a_device_off_is_what_its_next_report_is_told(tmp_path):
    client = client_for(tmp_path)
    report(client, "laptop")
    report(client, "desktop")

    response = client.patch("/control/devices/laptop", json={"capture": False})

    assert response.json()["devices"]["laptop"]["capture"] is False
    assert report(client, "laptop", capture="running") == {"capture": False}
    assert report(client, "desktop") == {"capture": True}


def test_only_a_device_that_has_reported_can_be_switched(tmp_path):
    response = client_for(tmp_path).patch("/control/devices/phone", json={"capture": False})
    assert response.status_code == 404


def test_the_switches_outlive_the_server(tmp_path):
    client = client_for(tmp_path)
    report(client, "laptop")
    client.patch("/control/devices/laptop", json={"capture": False})
    client.patch("/control/server", json={"annotate": False})

    control = client_for(tmp_path).get("/control").json()

    assert control["devices"]["laptop"]["capture"] is False
    assert control["server"]["annotate"] is False


def test_the_server_section_shows_the_annotation_queue(tmp_path):
    server = client_for(tmp_path).get("/control").json()["server"]
    assert server["annotate"] is True
    assert server["queue"]["running"] == 0
