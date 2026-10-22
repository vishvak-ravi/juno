"""The sessions API: each device's recordings listed whole, and one opened with its events."""

from test_events import DESKTOP, LAPTOP, client_for, put


def test_sessions_are_listed_newest_first_with_their_span_and_counts_but_not_their_events(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1500.0)
        put(client, DESKTOP, 0, 1200.0)

        listed = client.get("/sessions").json()
        assert [s["id"] for s in listed["data"]] == [DESKTOP, LAPTOP]
        assert listed["data"][1] == {"id": LAPTOP, "device_id": "laptop", "start": 1000.0,
                                     "end": 1500.0, "event_count": 2, "captioned": 0}

        only = client.get("/sessions", params={"device": "laptop"}).json()["data"]
        assert [s["id"] for s in only] == [LAPTOP]


def test_a_session_is_listed_for_a_range_it_overlaps(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1500.0)
        put(client, DESKTOP, 0, 2000.0)

        def ids(**params):
            return [s["id"] for s in client.get("/sessions", params=params).json()["data"]]

        assert ids(since=1200.0, until=1300.0) == [LAPTOP]
        assert ids(since=1600.0) == [DESKTOP]
        assert ids(until=900.0) == []


def test_sessions_page_in_either_order(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, DESKTOP, 0, 1200.0)

        first = client.get("/sessions", params={"order": "asc", "limit": 1}).json()
        assert [s["id"] for s in first["data"]] == [LAPTOP] and first["has_more"]
        rest = client.get("/sessions", params={"order": "asc", "limit": 1,
                                               "after": first["last_id"]}).json()
        assert [s["id"] for s in rest["data"]] == [DESKTOP] and not rest["has_more"]


def test_a_session_opens_with_every_event_oldest_first_without_deleted_ones(tmp_path):
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1100.0)
        put(client, LAPTOP, 2, 1200.0)
        client.delete(f"/events/{LAPTOP}-000001")

        session = client.get(f"/sessions/{LAPTOP}").json()
        assert [e["id"] for e in session["events"]] == [f"{LAPTOP}-000000", f"{LAPTOP}-000002"]
        assert session["events"][0] == client.get(f"/events/{LAPTOP}-000000").json()
        assert (session["start"], session["end"], session["event_count"]) == (1000.0, 1200.0, 2)


def test_an_unknown_session_is_not_found(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get(f"/sessions/{LAPTOP}").status_code == 404
        assert client.get("/sessions/not a session").status_code == 422
