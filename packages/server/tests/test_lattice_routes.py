"""The lattice API: builds are listed, started and watched; a stored lattice is read whole, per layer and per node."""

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from annotate.label import Action, Annotation
from juno.config import AnnotateConfig, CaptureConfig, Config, LatticeConfig, LayerSpec
from juno.conversations import CLAUDE_CODE, Conversation, Turn
from lattice.models import (ConversationRef, Edge, Insight, InsightLayer, Lattice, LatticeBuild,
                            LatticeParams, NodeMetadata, Observation, SourceInteraction, SourceSession)
from lattice.store import FileLattices, lattice_dir, new_build_id
from server.app import create_app
from server.conversations import FileConversations
from test_events import LAPTOP, put, wait_for_annotation

BUILD = "lattice_20260101_120000"
LAYERS = [LayerSpec(type="session", value="1"), LayerSpec(type="all")]


def params() -> LatticeParams:
    return LatticeParams(user_name="Sam", description="Sam's screen activities", window_size=2,
                         min_insights=1, session_gap_hours=3.0, layers=LAYERS)


def build_record(build_id: str = BUILD, status: str = "done") -> LatticeBuild:
    """A build over two sessions: the first has two interactions, the second one."""
    return LatticeBuild(
        id=build_id, device_id="laptop", since=None, until=None, status=status,
        created_at=1000.0, updated_at=1000.0, finished_at=1000.0 if status == "done" else None,
        models={"observer": "m", "insight": "m", "evidence": "m", "format": "m"},
        params=params(), captions=2,
        sessions=[
            SourceSession(index=0, start=1000.0, end=1001.0, interactions=[
                SourceInteraction(caption_id=f"{LAPTOP}-0000", action=0, time=1000.0),
                SourceInteraction(caption_id=f"{LAPTOP}-0001", action=0, time=1001.0)]),
            SourceSession(index=1, start=90000.0, end=90000.0, interactions=[
                SourceInteraction(caption_id=f"{LAPTOP}-0009", action=0, time=90000.0)])],
        node_counts=[3, 2, 1])


def observation(node_id: int, session: int) -> Observation:
    return Observation(id=node_id, observation=f"observation {node_id}", confidence=7,
                       metadata=NodeMetadata(input_session=session))


def insight(node_id: int, merged: list[int], session: int = 0) -> Insight:
    return Insight(id=node_id, title=f"insight {node_id}", tagline="", insight="", context="",
                   merged=merged, metadata=NodeMetadata(input_session=session))


def stored_lattice(build: LatticeBuild) -> Lattice:
    """Observations 0 and 1 from session 0, 2 from session 1; layer 1 insight 0 over 0 and 1,
    insight 1 over 2; layer 2's one insight over both."""
    return Lattice(
        build=build,
        observations=[observation(0, 0), observation(1, 0), observation(2, 1)],
        layers=[
            InsightLayer(spec=LAYERS[0], insights=[insight(0, [0, 1]), insight(1, [2], session=1)],
                         edges=[Edge(source=0, target=0), Edge(source=0, target=1),
                                Edge(source=1, target=2)]),
            InsightLayer(spec=LAYERS[1], insights=[insight(0, [0, 1])],
                         edges=[Edge(source=0, target=0), Edge(source=0, target=1)])])


def client_for(tmp_path, build_lattice=None, actions=(), dense_caption=""):
    def annotate_request(request):
        return Annotation(actions=list(actions), dense_caption=dense_caption,
                          sanitization=request.sanitization)
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2),
                    lattice=LatticeConfig(user_name="Sam", window_size=2, min_insights=1,
                                          session_gap_hours=3.0, layers=LAYERS))
    return TestClient(create_app(config, annotate_request=annotate_request,
                                 build_lattice=build_lattice))


def store(tmp_path) -> FileLattices:
    return FileLattices(lattice_dir(tmp_path))


def test_builds_are_listed_newest_first_without_their_sessions(tmp_path):
    store(tmp_path).save_build(build_record("lattice_20260101_120000"))
    store(tmp_path).save_build(build_record("lattice_20260102_120000", status="failed")
                              .model_copy(update={"created_at": 2000.0}))
    with client_for(tmp_path) as client:
        builds = client.get("/lattice").json()
    assert [(b["id"], b["status"], b["sessions"]) for b in builds] == [
        ("lattice_20260102_120000", "failed", 2), ("lattice_20260101_120000", "done", 2)]
    assert builds[0]["params"]["layers"] == [
        {"type": "session", "value": "1"}, {"type": "all", "value": None}]


def test_a_build_is_fetched_with_its_sessions(tmp_path):
    store(tmp_path).save_build(build_record())
    with client_for(tmp_path) as client:
        build = client.get(f"/lattice/{BUILD}").json()
        assert build["id"] == BUILD and [s["index"] for s in build["sessions"]] == [0, 1]
        assert build["sessions"][0]["interactions"][1]["caption_id"] == f"{LAPTOP}-0001"
        assert client.get("/lattice/lattice_20000101_000000").status_code == 404
        assert client.get("/lattice/not-a-build").status_code == 422


def test_the_whole_lattice_is_fetched_once_the_build_is_done(tmp_path):
    store(tmp_path).save_build(build_record(status="building"))
    with client_for(tmp_path) as client:
        assert client.get(f"/lattice/{BUILD}/lattice").status_code == 404
        store(tmp_path).save(stored_lattice(build_record()))
        lattice = client.get(f"/lattice/{BUILD}/lattice").json()
    assert [o["observation"] for o in lattice["observations"]] == [
        "observation 0", "observation 1", "observation 2"]
    assert [len(layer["insights"]) for layer in lattice["layers"]] == [2, 1]
    assert lattice["build"]["status"] == "done"


def test_each_layer_comes_with_its_spec_nodes_and_edges_down(tmp_path):
    store(tmp_path).save(stored_lattice(build_record()))
    with client_for(tmp_path) as client:
        bottom = client.get(f"/lattice/{BUILD}/layers/0").json()
        middle = client.get(f"/lattice/{BUILD}/layers/1").json()
        top = client.get(f"/lattice/{BUILD}/layers/2").json()
        assert client.get(f"/lattice/{BUILD}/layers/3").status_code == 404
    assert bottom["spec"] is None and bottom["edges"] == []
    assert [n["id"] for n in bottom["nodes"]] == [0, 1, 2]
    assert middle["spec"] == {"type": "session", "value": "1"}
    assert [n["title"] for n in middle["nodes"]] == ["insight 0", "insight 1"]
    assert middle["edges"] == [{"source": 0, "target": 0}, {"source": 0, "target": 1},
                               {"source": 1, "target": 2}]
    assert top["spec"] == {"type": "all", "value": None} and len(top["nodes"]) == 1


def test_a_node_shows_its_parents_children_support_and_the_events_behind_it(tmp_path):
    store(tmp_path).save(stored_lattice(build_record()))
    with client_for(tmp_path, actions=[Action(start=1, end=2, caption="Typed.")],
                    dense_caption="An editor.") as client:
        put(client, LAPTOP, 0, 1000.0)
        put(client, LAPTOP, 1, 1000.5)
        put(client, LAPTOP, 2, 1001.0)
        put(client, LAPTOP, 3, 1001.5)
        wait_for_annotation(tmp_path, LAPTOP, 0)
        wait_for_annotation(tmp_path, LAPTOP, 1)

        middle = client.get(f"/lattice/{BUILD}/nodes/1/0").json()
        top = client.get(f"/lattice/{BUILD}/nodes/2/0").json()
        leaf = client.get(f"/lattice/{BUILD}/nodes/0/2").json()
        assert client.get(f"/lattice/{BUILD}/nodes/1/5").status_code == 404
        assert client.get(f"/lattice/{BUILD}/nodes/3/0").status_code == 404

    assert middle["node"]["title"] == "insight 0"
    assert [p["title"] for p in middle["parents"]] == ["insight 0"]
    assert [c["id"] for c in middle["children"]] == [0, 1]
    assert {int(k): [n["id"] for n in v] for k, v in middle["support"].items()} == {0: [0, 1]}
    # Session 0's two interactions span frames 1-2 of chunks 0 and 1: rows 0-1 and 2-3.
    [session] = middle["sources"]
    assert session["index"] == 0 and (session["start"], session["end"]) == (1000.0, 1001.0)
    assert [i["event_ids"] for i in session["interactions"]] == [
        [f"{LAPTOP}-000000", f"{LAPTOP}-000001"], [f"{LAPTOP}-000002", f"{LAPTOP}-000003"]]
    assert [i["caption"] for i in session["interactions"]] == ["Typed.", "Typed."]

    assert top["parents"] == [] and [c["id"] for c in top["children"]] == [0, 1]
    assert {int(k): [n["id"] for n in v] for k, v in top["support"].items()} == {1: [0, 1], 0: [0, 1, 2]}
    assert [s["index"] for s in top["sources"]] == [0, 1]
    # Chunk 9 of the session has no caption, so the interaction's events are unknown.
    assert top["sources"][1]["interactions"][0]["event_ids"] == []
    assert top["sources"][1]["interactions"][0]["caption"] is None

    assert leaf["support"] == {} and leaf["children"] == []
    assert [p["id"] for p in leaf["parents"]] == [1]
    assert [s["index"] for s in leaf["sources"]] == [1]

def test_a_nodes_source_that_is_a_conversation_turn_shows_the_turns_text(tmp_path):
    turn = ConversationRef(harness=CLAUDE_CODE, device_id="laptop", session_id="thread-1", turn=1)
    build = build_record().model_copy(update={"sessions": [
        SourceSession(index=0, start=1000.0, end=1001.0, interactions=[
            SourceInteraction(conversation=turn, time=1000.0),
            SourceInteraction(conversation=turn.model_copy(update={"turn": 7}), time=1001.0)]),
        SourceSession(index=1, start=90000.0, end=90000.0, interactions=[
            SourceInteraction(caption_id=f"{LAPTOP}-0009", action=0, time=90000.0)])]})
    store(tmp_path).save(stored_lattice(build))
    FileConversations(tmp_path / "screen" / "devices").save(Conversation(
        harness=CLAUDE_CODE, session_id="thread-1", device_id="laptop", cwd="/w", entrypoint="cli",
        turns=[Turn(role="user", text="fix it", time=1000.0),
               Turn(role="assistant", text="Fixed.", time=1001.0)]))

    with client_for(tmp_path) as client:
        node = client.get(f"/lattice/{BUILD}/nodes/1/0").json()

    [session] = node["sources"]
    assert [i["caption"] for i in session["interactions"]] == ["Fixed.", None]
    assert [i["event_ids"] for i in session["interactions"]] == [[], []]
    assert session["interactions"][0]["conversation"] == {
        "harness": CLAUDE_CODE, "device_id": "laptop", "session_id": "thread-1", "turn": 1}
    assert session["interactions"][0]["caption_id"] is None


def wait_for_status(client: TestClient, build_id: str, status: str, seconds: float = 5.0) -> dict:
    """The build's record once its status is ``status``; fails if it is not within ``seconds``."""
    deadline = time.time() + seconds
    while True:
        record = client.get(f"/lattice/{build_id}").json()
        if record.get("status") == status or time.time() > deadline:
            assert record.get("status") == status, record
            return record
        time.sleep(0.02)


def test_a_started_build_is_accepted_and_shows_done_once_the_lattice_is_stored(tmp_path):
    seen = {}

    async def build_lattice(build, captions, conversations, lattices, config):
        seen["params"] = build.params
        lattice = stored_lattice(build.model_copy(update={"status": "done", "finished_at": time.time()}))
        lattices.save(lattice)
        return lattice

    with client_for(tmp_path, build_lattice) as client:
        response = client.post("/lattice", json={"device": "laptop", "since": 500.0, "min_insights": 4})
        assert response.status_code == 202, response.text
        accepted = response.json()
        assert accepted["status"] == "observing" and accepted["sessions"] == 0
        assert accepted["device_id"] == "laptop" and accepted["since"] == 500.0
        done = wait_for_status(client, accepted["id"], "done")
        assert client.get(f"/lattice/{accepted['id']}/lattice").status_code == 200
    assert done["id"] == accepted["id"]
    # The body's knob, the configured rest, the description naming the user.
    assert seen["params"].min_insights == 4 and seen["params"].window_size == 2
    assert seen["params"].description == "Sam's screen activities and conversations with coding assistants"
    assert [spec.type for spec in seen["params"].layers] == ["session", "all"]


def test_a_second_build_is_refused_while_one_runs(tmp_path):
    release = threading.Event()

    async def build_lattice(build, captions, conversations, lattices, config):
        await asyncio.get_running_loop().run_in_executor(None, release.wait)
        lattice = stored_lattice(build.model_copy(update={"status": "done"}))
        lattices.save(lattice)
        return lattice

    with client_for(tmp_path, build_lattice) as client:
        first = client.post("/lattice", json={})
        assert first.status_code == 202, first.text
        second = client.post("/lattice", json={})
        assert second.status_code == 409 and second.json()["detail"]["build_id"] == first.json()["id"]
        release.set()
        wait_for_status(client, first.json()["id"], "done")
        third = client.post("/lattice", json={})
        assert third.status_code == 202, third.text
        wait_for_status(client, third.json()["id"], "done")


def test_a_build_that_raises_is_recorded_as_failed(tmp_path):

    async def build_lattice(build, captions, conversations, lattices, config):
        raise RuntimeError("the observer model is unreachable")

    with client_for(tmp_path, build_lattice) as client:
        build_id = client.post("/lattice", json={}).json()["id"]
        failed = wait_for_status(client, build_id, "failed")
        assert "unreachable" in failed["error"]
        assert client.get(f"/lattice/{build_id}/lattice").status_code == 404
