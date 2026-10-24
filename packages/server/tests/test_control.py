"""The control API: switches clients set, and what recorders report and are told."""

import time

from annotate.store import FileActivityCaptions
from juno.config import devices_dir
from test_events import LAPTOP, client_for, put, wait_for_annotation


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


def test_with_annotation_off_chunks_wait_until_it_is_on_again(tmp_path):
    client = client_for(tmp_path)
    client.patch("/control/server", json={"annotate": False})
    put(client, LAPTOP, 0, 1000.0)
    put(client, LAPTOP, 1, 1001.0)
    put(client, LAPTOP, 2, 1002.0)  # the row after chunk 0 queues it

    time.sleep(0.3)
    record = FileActivityCaptions(devices_dir(tmp_path)).get(LAPTOP, 0)
    assert record.status == "pending"
    assert client.get("/control").json()["server"]["queue"]["queued"]["live"] == 1

    client.patch("/control/server", json={"annotate": True})
    wait_for_annotation(tmp_path, LAPTOP, 0)
