"""The proposals API: a day is proposed over, its proposals listed by state, decided on, and the rate counted."""

import datetime
import time

from fastapi.testclient import TestClient

from juno.config import CaptureConfig, Config, DawnConfig, LatticeConfig, ProposalsConfig
from lattice.proposals import FileProposals, proposals_dir
from server.app import create_app
from server.propose import seconds_until
from test_lattice_routes import build_record, params
from test_proposals import DAY, REPLIES, config as dawn_config, fake_call, stores

RUN_ID = r"dawn_\d{8}_\d{6}"


def client_for(tmp_path, replies=REPLIES, almost_seconds=30.0):
    config = Config(capture=CaptureConfig(data_dir=tmp_path), lattice=LatticeConfig(user_name="Ada", layers=params().layers),
                    dawn=dawn_config(), proposals=ProposalsConfig(propose_at=None, almost_seconds=almost_seconds))
    return TestClient(create_app(config, dawn_call=fake_call(replies)))


def wait_for_status(client, run_id, status, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        run = client.get(f"/proposals/runs/{run_id}").json()
        if run["status"] == status:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach {status}")


def proposed(client):
    """One finished run over DAY, started through ``client``."""
    started = client.post("/proposals/runs", json={"day": DAY})
    assert started.status_code == 202, started.text
    return wait_for_status(client, started.json()["id"], "done")


def test_a_run_is_started_over_a_day_and_lists_what_it_came_to(tmp_path):
    stores(tmp_path)
    with client_for(tmp_path) as client:
        assert client.post("/proposals/runs", json={"day": "2026-1-5"}).status_code == 422
        started = client.post("/proposals/runs", json={"day": DAY, "utility_threshold": 0.8})
        assert started.status_code == 202, started.text
        assert started.json()["status"] == "running" and started.json()["utility_threshold"] == 0.8
        run = wait_for_status(client, started.json()["id"], "done")
        runs = client.get("/proposals/runs").json()
    assert [t["task"] for t in run["tasks"]] == ["Writing a paper", "Sorting a list"]
    assert run["near_threshold"] == 0.1 and set(run["states"]) == {f"{run['id']}-00-0", f"{run['id']}-00-1"}
    assert run["states"][f"{run['id']}-00-0"]["state"] == "new"
    assert [(r["id"], r["tasks"], r["asked"], r["proposals"], r["accepted"], r["open"]) for r in runs] == [
        (run["id"], 2, 1, 2, 0, 2)]


def test_a_second_run_waits_and_a_day_without_a_lattice_is_refused(tmp_path):
    with client_for(tmp_path) as client:
        refused = client.post("/proposals/runs", json={"day": DAY})
        assert refused.status_code == 409 and "no finished lattice" in refused.json()["detail"]


def test_proposals_are_listed_newest_run_first_and_marked_listed(tmp_path):
    stores(tmp_path)
    with client_for(tmp_path) as client:
        run = proposed(client)
        page = client.get("/proposals").json()
        assert [(p["id"], p["title"], p["bucket"]) for p in page["data"]] == [
            (f"{run['id']}-00-1", "Timebox", "open"), (f"{run['id']}-00-0", "Outline", "open")]
        assert page["data"][0]["task"] == {"index": 0, "task": "Writing a paper", "score": 0.9, "asked_by": "threshold"}
        assert page["data"][0]["insight_ids"] == [0] and page["data"][0]["day"] == DAY
        assert client.get("/proposals?state=accepted").json()["data"] == []
        # A run over every device's captions is every device's.
        assert len(client.get("/proposals?device=phone").json()["data"]) == 2
        one = client.get(f"/proposals/{run['id']}-00-0").json()
    assert [d["event"] for d in one["trail"]] == ["listed"] and one["trail"][0]["where"] == "server"
    assert one["whole_task"]["ranked"] == [{"id": 0, "score": 0.6, "given": True}]
    assert one["whole_task"]["considered"] == [{"title": "Rewrite it", "why": "needs the user's judgement"}]
    assert [i["title"] for i in one["insights"]] == ["insight 0"]
    assert one["run"]["sessions"][0]["actions"] == 3 and one["run"]["proposals"] == 2
    by_what = {(c["step"], c["what"]): c for c in one["calls"]}
    assert by_what[("actions", "task 1")]["prompt"] is not None and by_what[("utility", "task 2")]["prompt"] is None
    assert FileProposals(proposals_dir(tmp_path)).trails(run["id"])[f"{run['id']}-00-0"][0].event == "listed"


def test_a_decision_joins_the_trail_and_changes_the_state(tmp_path):
    stores(tmp_path)
    with client_for(tmp_path) as client:
        run = proposed(client)
        first, second = f"{run['id']}-00-0", f"{run['id']}-00-1"
        client.get("/proposals")
        opened = client.patch(f"/proposals/{first}", json={"event": "opened", "where": "desktop", "dwell": 45})
        assert opened.status_code == 200 and opened.json()["state"]["state"] == "seen"
        no = client.patch(f"/proposals/{first}", json={"event": "dismissed", "where": "phone", "detail": "later"}).json()
        assert (no["state"]["state"], no["state"]["almost"], no["bucket"]) == ("dismissed", True, "almost")
        yes = client.patch(f"/proposals/{second}", json={"event": "accepted", "where": "desktop"}).json()
        assert yes["state"]["state"] == "accepted" and yes["state"]["decided"]["where"] == "desktop"
        assert [p["id"] for p in client.get("/proposals?state=almost").json()["data"]] == [first]
        assert [p["id"] for p in client.get("/proposals?state=accepted").json()["data"]] == [second]
        assert client.get("/proposals?state=open").json()["data"] == []
        rate = client.get("/proposals/rate").json()
        assert rate == {"shown": 2, "decided": 2, "accepted": 1, "almost": 1, "open": 0}
        assert client.get(f"/proposals/rate?since={time.time() + 100}").json()["shown"] == 0
        back = client.patch(f"/proposals/{first}", json={"event": "reopened", "where": "desktop"}).json()
        assert back["state"]["state"] == "seen" and back["bucket"] == "open"
        assert client.patch(f"/proposals/{run['id']}-05-0", json={"event": "opened", "where": "desktop"}).status_code == 404
        assert client.patch(f"/proposals/{first}", json={"event": "listed", "where": "desktop"}).status_code == 422
        assert client.get("/proposals/runs").json()[0]["accepted"] == 1


def test_a_task_under_the_line_is_proposed_for_on_request(tmp_path):
    stores(tmp_path)
    with client_for(tmp_path) as client:
        run = proposed(client)
        assert client.post(f"/proposals/runs/{run['id']}/tasks/7").status_code == 404
        again = client.post(f"/proposals/runs/{run['id']}/tasks/1")
        assert again.status_code == 200, again.text
        task = again.json()["tasks"][1]
        assert task["asked"] and task["asked_by"] == "user" and [a["title"] for a in task["actions"]] == ["Outline", "Timebox"]
        assert f"{run['id']}-01-0" in again.json()["states"]
        assert len(client.get("/proposals").json()["data"]) == 4


def test_the_schedule_waits_for_the_next_time_of_day():
    now = datetime.datetime(2026, 1, 5, 4, 30)
    assert seconds_until(now, "05:00") == 1800
    assert seconds_until(now, "04:00") == 23.5 * 3600
