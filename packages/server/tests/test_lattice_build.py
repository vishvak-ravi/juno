"""The lattice build: interactions, sessions, observations, layers and the record it keeps."""

import asyncio
import json
import re
from datetime import datetime

import pytest
from PIL import Image

from annotate.label import Action, Annotation
from annotate.request import AnnotationFrame
from annotate.sanitize import Sanitization
from annotate.store import FileActivityCaptions, new_caption
from juno.config import LatticeConfig, LayerSpec, devices_dir
from juno.conversations import CLAUDE_CODE, Conversation, Turn
from juno.events import ACTIVITY, MESSAGE
from juno.sessions import session_dir
from lattice.build import build_lattice, new_build
from lattice.llm import Reply
from lattice.models import LatticeParams
from lattice.prompts import (FORMAT_INSIGHT_PROMPT, INSIGHT_SYNTHESIS_PROMPT, MAP_EVIDENCE_PROMPT,
                             OBSERVATION_TO_INSIGHT_PROMPT, OBSERVE_PROMPT)
from lattice.store import FileLattices, lattice_dir
from server.conversations import FileConversations



def head(prompt):
    """What a prompt starts with before its first placeholder, enough to tell it from the others."""
    return prompt[:prompt.index("{")]


SANITIZATION = Sanitization(algorithm="none", version=1, text=False, images=False, redactions={})
SESSION = "laptop-session_20260101_120000"
HOUR = 3600.0
# Local noon on a weekday, so a session and its day are one thing in the tests.
DAY_ONE = datetime(2026, 3, 2, 12, 0).timestamp()


def write_caption(tmp_path, chunk, captions, dense_caption, times, status="done"):
    """A caption with one frame per action, frame ``n`` taken at ``times[n]``."""
    directory = session_dir(devices_dir(tmp_path), SESSION)
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for number, at in enumerate(times, start=1):
        path = directory / f"{chunk}-{number}.png"
        Image.new("RGB", (8, 8), "white").save(path)
        frames.append(AnnotationFrame(label=f"Frame {number}", screenshot_path=str(path),
                                      timestamp=at, cursor=None, monitor=None, events=""))
    actions = [Action(start=number, end=number, caption=caption)
               for number, caption in enumerate(captions, start=1)]
    record = new_caption(SESSION, chunk, "fake-annotator").model_copy(update={
        "status": status, "frames": frames, "start_time": times[0], "end_time": times[-1],
        "annotation": Annotation(actions=actions, dense_caption=dense_caption,
                                 sanitization=SANITIZATION)})
    FileActivityCaptions(devices_dir(tmp_path)).save(record)


def write_conversation(tmp_path, session_id, turns, device_id="laptop"):
    """A Claude Code conversation of ``turns``: (role, text, time) triples."""
    FileConversations(devices_dir(tmp_path)).save(Conversation(
        harness=CLAUDE_CODE, session_id=session_id, device_id=device_id, cwd="/home/ada/proj",
        entrypoint="cli", turns=[Turn(role=role, text=text, time=at) for role, text, at in turns]))


def params(**overrides):
    values = {"user_name": "Ada", "description": "Ada's screen activities", "window_size": 2,
              "min_insights": 1, "session_gap_hours": 3.0,
              "layers": [LayerSpec(type="time", value="day"), LayerSpec(type="all")]}
    return LatticeParams(**{**values, **overrides})


class FakeModel:
    """Answers each prompt by the prompt it was made from, recording what it was sent."""

    def __init__(self, fail=None):
        self.prompts = []
        # Called with (prompt, schema); True makes that call raise.
        self.fail = fail

    async def __call__(self, model, prompt, schema):
        self.prompts.append((model, prompt, schema))
        if self.fail is not None and self.fail(prompt, schema):
            raise RuntimeError("model down")
        return Reply(text=self.answer(prompt), prompt_tokens=10, completion_tokens=5)

    def answer(self, prompt):
        if prompt.startswith(head(OBSERVE_PROMPT)):
            window = prompt.split("Here is a summary of", 1)[1]
            first = [line for line in window.splitlines()
                     if line.startswith(("Did ", "Ada: ", "Claude Code: "))][0]
            return json.dumps({"observations": [
                {"think_feel": f"Feels focused after: {first}", "actions": f"Evidence: {first}",
                 "confidence": "6"}]})
        if prompt.startswith(head(OBSERVATION_TO_INSIGHT_PROMPT)):
            ids = [line.split(" | ")[0][4:] for line in prompt.splitlines() if line.startswith("ID: ")]
            return "Insight prose built on observations " + ",".join(ids)
        if prompt.startswith(head(FORMAT_INSIGHT_PROMPT)):
            ids = re.search(r"observations ([\d,]+)", prompt).group(1)
            return json.dumps({"insights": [
                {"title": f"Insight over {ids}", "tagline": "t", "insight": "i", "context": "c",
                 "supporting_evidence": f"observations {ids}"}]})
        if prompt.startswith(head(MAP_EVIDENCE_PROMPT)):
            ids = re.search(r"observations ([\d,]+)", prompt).group(1)
            return json.dumps({"supporting_ids": [int(i) for i in ids.split(",")]})
        if prompt.startswith(head(INSIGHT_SYNTHESIS_PROMPT)):
            ids = [line.split(" | ")[0][4:] for line in prompt.splitlines() if line.startswith("ID: ")]
            return "```json\n" + json.dumps({"insights": [
                {"title": "Top", "tagline": "t", "insight": "i", "context": "c",
                 "merged": ids, "supporting_evidence": "why", "reasoning": "extra"}]}) + "\n```"
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")


def run(tmp_path, model, build_params=None, device=None):
    config = LatticeConfig()
    build = new_build(config, build_params or params(), device, None, None)
    lattices = FileLattices(lattice_dir(tmp_path))
    captions = FileActivityCaptions(devices_dir(tmp_path))
    conversations = FileConversations(devices_dir(tmp_path))
    lattice = asyncio.run(build_lattice(build, captions, conversations, lattices, config, call=model))
    return lattice, lattices


def test_interactions_are_one_per_action_with_the_dense_caption_in_time_order(tmp_path):
    write_caption(tmp_path, 1, ["Did B", "Did C"], "Dense one.", [DAY_ONE + 60, DAY_ONE + 120])
    write_caption(tmp_path, 0, ["Did A"], "Dense zero.", [DAY_ONE])
    write_caption(tmp_path, 2, ["Did X"], "Dense two.", [DAY_ONE + 180], status="failed")
    model = FakeModel()
    lattice, _ = run(tmp_path, model, params(window_size=10))

    [session] = lattice.build.sessions
    assert [(i.caption_id, i.action, i.time) for i in session.interactions] == [
        (f"{SESSION}-0000", 0, DAY_ONE), (f"{SESSION}-0001", 0, DAY_ONE + 60),
        (f"{SESSION}-0001", 1, DAY_ONE + 120)]
    [(_, observe_prompt, _)] = [p for p in model.prompts if p[1].startswith(head(OBSERVE_PROMPT))]
    stamp = datetime.fromtimestamp(DAY_ONE).strftime("%Y-%m-%d %H:%M:%S")
    assert observe_prompt.endswith(
        f"time\n{stamp}\nDid A Dense zero.\n"
        f"time\n{datetime.fromtimestamp(DAY_ONE + 60).strftime('%Y-%m-%d %H:%M:%S')}\nDid B Dense one.\n"
        f"time\n{datetime.fromtimestamp(DAY_ONE + 120).strftime('%Y-%m-%d %H:%M:%S')}\nDid C Dense one.\n")
    assert "Ada's screen activities for the user, Ada" in observe_prompt
    assert lattice.build.captions == 2


def test_sessions_are_cut_at_the_gap_and_observations_carry_their_session_and_time(tmp_path):
    write_caption(tmp_path, 0, ["Did A", "Did B"], "D.", [DAY_ONE, DAY_ONE + 2 * HOUR])
    write_caption(tmp_path, 1, ["Did C"], "D.", [DAY_ONE + 5 * HOUR])
    lattice, _ = run(tmp_path, FakeModel(), params(window_size=1))

    assert [(s.index, s.start, s.end) for s in lattice.build.sessions] == [
        (0, DAY_ONE, DAY_ONE + 2 * HOUR), (1, DAY_ONE + 5 * HOUR, DAY_ONE + 5 * HOUR)]
    first = datetime.fromtimestamp(DAY_ONE).strftime("%Y-%m-%d %H:%M:%S")
    second = datetime.fromtimestamp(DAY_ONE + 5 * HOUR).strftime("%Y-%m-%d %H:%M:%S")
    # Each observation is two nodes (think_feel, actions), ids counting across the layer.
    assert [(o.id, o.metadata.input_session, o.metadata.time, o.confidence)
            for o in lattice.observations] == [
        (0, 0, first, 6), (1, 0, first, 6), (2, 0, first, 6), (3, 0, first, 6),
        (4, 1, second, 6), (5, 1, second, 6)]
    assert lattice.observations[0].observation.startswith("Feels focused after: Did A")
    assert lattice.observations[1].observation.startswith("Evidence: Did A")


def test_layer_one_edges_come_from_the_evidence_mapping_and_higher_layers_from_merged(tmp_path):
    write_caption(tmp_path, 0, ["Did A", "Did B"], "D.", [DAY_ONE, DAY_ONE + 60])
    write_caption(tmp_path, 1, ["Did C"], "D.", [DAY_ONE + 2 * 24 * HOUR])
    model = FakeModel()
    lattice, _ = run(tmp_path, model, params(window_size=10))

    day, top = lattice.layers
    assert day.spec == LayerSpec(type="time", value="day")
    assert [(i.id, i.metadata.input_session, i.merged) for i in day.insights] == [
        (0, 0, [0, 1]), (1, 1, [2, 3])]
    assert [(e.source, e.target) for e in day.edges] == [(0, 0), (0, 1), (1, 2), (1, 3)]
    assert day.insights[0].supporting_evidence == ["observations 0,1"]
    assert day.insights[0].metadata.time == lattice.observations[0].metadata.time
    assert day.insights[1].metadata.time == lattice.observations[2].metadata.time

    assert top.spec == LayerSpec(type="all")
    [insight] = top.insights
    assert (insight.title, insight.merged, insight.supporting_evidence) == ("Top", [0, 1], ["why"])
    assert [(e.source, e.target) for e in top.edges] == [(0, 0), (0, 1)]
    # The quirks of the library's formatting are sent as they are.
    [(_, format_prompt, schema)] = [p for p in model.prompts if p[1].startswith(head(FORMAT_INSIGHT_PROMPT))
                                    and "observations 0,1" in p[1]]
    assert "$Insight prose built on observations 0,1\n" in format_prompt
    assert "insights" in schema["properties"]
    [(_, synthesis_prompt, _)] = [p for p in model.prompts if p[1].startswith(head(INSIGHT_SYNTHESIS_PROMPT))]
    assert "observing $Ada along with" in synthesis_prompt
    assert "ID: 0 | Insight over 0,1: i | Context Insight Applies: c\n" in synthesis_prompt


def test_a_failed_call_costs_only_its_window_or_group(tmp_path):
    write_caption(tmp_path, 0, ["Did A", "Did B"], "D.", [DAY_ONE, DAY_ONE + 60])
    write_caption(tmp_path, 1, ["Did C"], "D.", [DAY_ONE + 2 * 24 * HOUR])

    def fail(prompt, schema):
        return prompt.startswith(head(OBSERVE_PROMPT)) and "Did C" in prompt
    lattice, _ = run(tmp_path, FakeModel(fail=fail), params(window_size=10))
    assert [o.metadata.input_session for o in lattice.observations] == [0, 0]
    assert lattice.build.stage_failures["observe"] == 1
    assert lattice.build.stage_calls["observe"] == 2

    def fail_group(prompt, schema):
        return prompt.startswith(head(OBSERVATION_TO_INSIGHT_PROMPT)) and "ID: 2 " in prompt
    lattice, _ = run(tmp_path, FakeModel(fail=fail_group), params(window_size=10))
    assert [i.metadata.input_session for i in lattice.layers[0].insights] == [0]
    assert lattice.build.stage_failures["layer_1"] == 1
    assert lattice.build.stage_failures["layer_2"] == 0


def test_the_record_moves_through_observing_building_and_done_with_its_counts(tmp_path):
    write_caption(tmp_path, 0, ["Did A", "Did B", "Did C"], "D.", [DAY_ONE, DAY_ONE + 60, DAY_ONE + 120])
    statuses = []
    build = new_build(LatticeConfig(), params(), None, None, None)
    lattices = FileLattices(lattice_dir(tmp_path))

    class Watching(FileLattices):
        def save_build(self, record):
            statuses.append((record.status, list(record.node_counts), len(record.sessions)))
            super().save_build(record)
    watching = Watching(lattice_dir(tmp_path))
    lattice = asyncio.run(build_lattice(build, FileActivityCaptions(devices_dir(tmp_path)),
                                        FileConversations(devices_dir(tmp_path)), watching,
                                        LatticeConfig(), call=FakeModel()))

    # Saved when started, once the captions are read into sessions, then after every stage.
    assert statuses == [("observing", [], 0), ("observing", [], 1), ("observing", [4], 1),
                        ("building", [4, 1], 1), ("building", [4, 1, 1], 1), ("done", [4, 1, 1], 1)]
    assert lattice.build.node_counts == [4, 1, 1]
    assert set(lattice.build.stage_seconds) == {"observe", "layer_1", "layer_2"}
    # Two windows; one prose, one format and one evidence call; one synthesis.
    assert lattice.build.stage_calls == {"observe": 2, "layer_1": 3, "layer_2": 1}
    assert lattice.build.stage_failures == {"observe": 0, "layer_1": 0, "layer_2": 0}
    assert lattice.build.finished_at is not None
    assert lattices.get(build.id) == lattice
    assert lattices.get_build(build.id).status == "done"


def test_an_exception_leaves_the_record_failed_with_the_error(tmp_path):
    write_caption(tmp_path, 0, ["Did A"], "D.", [DAY_ONE])
    build = new_build(LatticeConfig(), params(layers=[LayerSpec(type="number")]), None, None, None)
    lattices = FileLattices(lattice_dir(tmp_path))
    with pytest.raises(ValueError):
        asyncio.run(build_lattice(build, FileActivityCaptions(devices_dir(tmp_path)),
                                        FileConversations(devices_dir(tmp_path)), lattices,
                                  LatticeConfig(), call=FakeModel()))
    stored = lattices.get_build(build.id)
    assert stored.status == "failed"
    assert "number layer needs a value" in stored.error
    assert lattices.get(build.id) is None


def test_a_reply_that_is_not_json_is_fixed_by_the_format_model(tmp_path):
    write_caption(tmp_path, 0, ["Did A"], "D.", [DAY_ONE])
    inner = FakeModel()

    async def model(name, prompt, schema):
        if prompt.startswith(head(OBSERVE_PROMPT)):
            return Reply(text="Sure! Here you go: observations about Ada, no JSON though")
        if prompt.startswith("The following is malformed JSON"):
            assert schema["properties"]["observations"]
            return Reply(text=json.dumps({"observations": [
                {"think_feel": "Fixed", "actions": "Fixed evidence", "confidence": 3}]}))
        return await inner(name, prompt, schema)
    lattice, _ = run(tmp_path, model, params(window_size=10))
    assert [o.observation for o in lattice.observations] == ["Fixed", "Fixed evidence"]
    assert lattice.build.stage_calls["observe"] == 2
    assert lattice.build.stage_failures["observe"] == 0


def test_a_conversation_is_one_session_of_turns_read_as_speaker_and_text_among_the_activity_sessions(tmp_path):
    write_caption(tmp_path, 0, ["Did A"], "Dense zero.", [DAY_ONE])
    write_conversation(tmp_path, "thread-1", [
        ("assistant", "Done.", DAY_ONE + 5 * HOUR + 30),
        ("user", "fix the test", DAY_ONE + 5 * HOUR),
        ("user", "x" * 30, DAY_ONE + 5 * HOUR + 60)])
    write_conversation(tmp_path, "thread-0", [("user", "earlier thread", DAY_ONE - HOUR)])
    model = FakeModel()
    lattice, _ = run(tmp_path, model, params(window_size=10, turn_chars=20))

    # Sessions by first interaction: the earlier thread, the activity, the later thread.
    sources = [[(i.caption_id, i.conversation.session_id if i.conversation else None,
                 i.conversation.turn if i.conversation else None) for i in s.interactions]
               for s in lattice.build.sessions]
    assert sources == [[(None, "thread-0", 0)], [(f"{SESSION}-0000", None, None)],
                       [(None, "thread-1", 1), (None, "thread-1", 0), (None, "thread-1", 2)]]
    windows = [p[1] for p in model.prompts if p[1].startswith(head(OBSERVE_PROMPT))]
    assert len(windows) == 3
    stamp = lambda at: datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M:%S")
    assert windows[2].endswith(
        f"time\n{stamp(DAY_ONE + 5 * HOUR)}\nAda: fix the test\n"
        f"time\n{stamp(DAY_ONE + 5 * HOUR + 30)}\nClaude Code: Done.\n"
        f"time\n{stamp(DAY_ONE + 5 * HOUR + 60)}\nAda: {'x' * 20} [...]\n")
    assert lattice.build.captions == 1
    assert lattice.build.conversations == 2 and lattice.build.turns == 4
    # The observations of a thread carry its session and its start, like an activity session's.
    thread = [o for o in lattice.observations if o.metadata.input_session == 2]
    assert thread and all(o.metadata.time == stamp(DAY_ONE + 5 * HOUR) for o in thread)


def test_only_the_event_types_the_build_names_are_read(tmp_path):
    write_caption(tmp_path, 0, ["Did A"], "D.", [DAY_ONE])
    write_conversation(tmp_path, "thread-1", [("user", "fix the test", DAY_ONE + HOUR)])

    activity, _ = run(tmp_path, FakeModel(), params(events=[ACTIVITY]))
    assert [i.caption_id for s in activity.build.sessions for i in s.interactions] == [f"{SESSION}-0000"]
    assert activity.build.conversations == 0 and activity.build.turns == 0

    messages, _ = run(tmp_path, FakeModel(), params(events=[MESSAGE]))
    assert [i.conversation.session_id for s in messages.build.sessions for i in s.interactions] == ["thread-1"]
    assert messages.build.captions == 0


def test_a_build_over_one_device_and_a_time_range_reads_that_devices_conversations_in_it(tmp_path):
    write_conversation(tmp_path, "mine", [("user", "in range", DAY_ONE)])
    write_conversation(tmp_path, "old", [("user", "before", DAY_ONE - 2 * HOUR)])
    write_conversation(tmp_path, "theirs", [("user", "other device", DAY_ONE)], device_id="phone")
    config = LatticeConfig()
    build = new_build(config, params(events=[MESSAGE]), "laptop", DAY_ONE - HOUR, DAY_ONE + HOUR)
    lattice = asyncio.run(build_lattice(build, FileActivityCaptions(devices_dir(tmp_path)),
                                        FileConversations(devices_dir(tmp_path)),
                                        FileLattices(lattice_dir(tmp_path)), config, call=FakeModel()))
    assert [s.interactions[0].conversation.session_id for s in lattice.build.sessions] == ["mine"]
