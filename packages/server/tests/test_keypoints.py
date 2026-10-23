"""Jev's answers per row: which rows are asked what, and the indices and moments they become."""

import json
import time

from fastapi.testclient import TestClient
from httpx2 import Headers
from PIL import Image
from typesafe_sdk import TypeSafeBadRequestError

from annotate.label import Action, Annotation
from annotate.request import build_request
from annotate.sanitize import sanitize
from annotate.store import new_caption, write_caption_file
from juno.config import AnnotateConfig, CaptureConfig, Config, KeypointsConfig, devices_dir
from juno.sessions import session_dir
from keypoints.derive import session_moments
from keypoints.questions import load_questions
from keypoints.store import FileQuestionAnswers, NoulAnswer, RowAnswers
from server.app import create_app
from server.events import read_rows

SESSION = "laptop-session_20260101_120000"
QUESTIONS = load_questions()
ON, OFF = 0.7, 0.4
DETAILS = {"key_press": {"key": "a"}, "mouse_down": {"button": "left", "x": None, "y": None}}


def noul(p: float) -> NoulAnswer:
    return NoulAnswer(type="noul", noul=p)


def moments_of(rows):
    found = session_moments(rows, QUESTIONS, ON, OFF)
    return {row: [m.type for m in moments] for row, moments in found.items()}


def test_something_left_on_screen_is_one_moment_until_it_goes_away():
    rows = {0: {"error_shown": noul(0.9)}, 1: {"error_shown": noul(0.8)},
            2: {"error_shown": noul(0.5)}, 3: {"error_shown": noul(0.9)},
            4: {"error_shown": noul(0.1)}, 5: {"error_shown": noul(0.95)}}
    assert moments_of(rows) == {0: ["error_shown"], 5: ["error_shown"]}


def test_a_row_without_the_answer_does_not_end_what_is_on_screen():
    rows = {0: {"error_shown": noul(0.9)}, 1: {}, 2: {"error_shown": noul(0.9)}}
    assert moments_of(rows) == {0: ["error_shown"]}


def test_an_action_in_consecutive_rows_is_a_moment_in_each():
    rows = {0: {"code_landed": noul(0.9)}, 1: {"code_landed": noul(0.8)},
            2: {"code_landed": noul(0.2)}}
    assert moments_of(rows) == {0: ["code_landed"], 1: ["code_landed"]}


def test_the_assistant_is_named_only_when_one_is_in_focus():
    harness = {"type": "choice", "choice": "codex", "confidence": 0.9,
               "probabilities": {"codex": 0.95, "other": 0.05}}
    rows = {0: {"agent_prompt_submitted": noul(0.9), "agent": noul(0.9),
                "agent_harness": harness},
            1: {"agent_prompt_submitted": noul(0.9), "agent": noul(0.2),
                "agent_harness": harness}}
    rows = {row: RowAnswers.model_validate({"answers": answers}).answers
            for row, answers in rows.items()}
    found = session_moments(rows, QUESTIONS, ON, OFF)
    assert [m.label for m in found[0]] == ["codex"]
    assert [m.label for m in found[1]] == [None]


# -- the worker and the routes ------------------------------------------------

def write_session(tmp_path, inputs_per_row):
    """A stored session, one row a second from t=1000, with a done annotation per chunk of 2."""
    directory = session_dir(devices_dir(tmp_path), SESSION)
    directory.mkdir(parents=True)
    lines = []
    for index, inputs in enumerate(inputs_per_row):
        path = directory / f"{index}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        at = 1000.0 + index
        events = [{"timestamp": at + 0.1, "monitor_index": 0, "monitor": None,
                   "event_type": event_type, "details": DETAILS[event_type],
                   "cursor_position": None}
                  for event_type in inputs]
        lines.append(json.dumps({
            "timestamp": at, "reason": "key_start", "event_type": "key",
            "request_state": "start", "screenshot_path": str(path),
            "screenshot_timestamp": at, "end_screenshot_timestamp": at + 1.0,
            "num_events": len(events), "events": events, "cursor_position": None,
            "monitor": None, "burst_id": 1, "scale_factor": 1.0}))
    (directory / "event_rows.jsonl").write_text("\n".join(lines) + "\n")
    aggregations = read_rows(directory)
    for chunk in range(-(-len(aggregations) // 2)):
        request = sanitize(build_request(aggregations[chunk * 2:chunk * 2 + 2], prompt=""))
        annotation = Annotation(actions=[Action(start=1, end=len(request.frames), caption="Typed.")],
                                dense_caption="", sanitization=request.sanitization)
        record = new_caption(SESSION, chunk, "test").model_copy(update={
            "status": "done", "annotation": annotation, "frames": request.frames,
            "rows": len(request.frames), "start_time": request.frames[0].timestamp,
            "end_time": request.frames[-1].timestamp})
        write_caption_file(directory, record)
    return directory


class FakeJev:
    """Says yes to the error and to every action; answers the rest no."""

    def __init__(self):
        self.asked = []

    async def __call__(self, state, questions):
        self.asked.append((state["caption"], sorted(questions)))
        answers = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.9 if key != "agent" else 0.1}
            elif question["type"] == "score":
                levels = len(question["criteria"])
                answers[key] = {"type": "score", "score": 0.0, "confidence": 1.0,
                                "probabilities": {str(i): float(i == 0) for i in range(levels)}}
            else:
                first = next(iter(question["criteria"]))
                answers[key] = {"type": "choice", "choice": first, "confidence": 1.0,
                                "probabilities": {first: 1.0}}
        return RowAnswers.model_validate({"answers": answers, "input_tokens": 10})


def client_for(tmp_path, jev):
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    annotate=AnnotateConfig(chunk_size=2, scan_interval_seconds=0.05),
                    keypoints=KeypointsConfig(moment_on=ON, moment_off=OFF, bucket_seconds=2))
    return TestClient(create_app(config, annotate_request=lambda r: None, ask=jev))


def wait_for_answers(tmp_path, chunks):
    answers = FileQuestionAnswers(devices_dir(tmp_path))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(answers.get(SESSION, chunk) is not None for chunk in range(chunks)):
            return
        time.sleep(0.02)
    raise AssertionError("the chunks were not answered")


def test_every_annotated_row_gets_indices_and_moments(tmp_path):
    directory = write_session(tmp_path, [["key_press"], [], ["mouse_down"]])
    jev = FakeJev()
    with client_for(tmp_path, jev) as client:
        wait_for_answers(tmp_path, 2)
        events = client.get("/events", params={"order": "asc"}).json()["data"]

    action = set(QUESTIONS.action)
    asked = [set(questions) for _, questions in jev.asked]
    assert len(asked) == 3 and all(q >= set(QUESTIONS.screen) for q in asked)
    # The idle row is asked about the screen only.
    assert sorted(bool(q & action) for q in asked) == [False, True, True]
    assert {caption for caption, _ in jev.asked} == {"Typed."}

    assert [e["indices"] for e in events] == [{"sensitivity": 0.0, "agent": 0.1, "error": 0.9}] * 3
    types = [[m["type"] for m in e["moments"]] for e in events]
    # The error stays on screen: one moment. Actions: one on each active row.
    assert types[0] == ["agent_needs_input", "error_shown"] + QUESTIONS.moments.action
    assert types[1] == []
    assert types[2] == QUESTIONS.moments.action
    assert all(m["detector"] == "jev" and m["label"] is None for e in events for m in e["moments"])


def test_indices_are_averaged_per_bucket(tmp_path):
    directory = write_session(tmp_path, [["key_press"], [], ["mouse_down"]])
    with client_for(tmp_path, FakeJev()) as client:
        wait_for_answers(tmp_path, 2)
        indices = client.get("/indices").json()
        wide = client.get("/indices", params={"bucket_seconds": 60}).json()

    assert indices["names"] == ["sensitivity", "agent", "error"]
    assert [(b["start"], b["rows"], b["detected"]) for b in indices["buckets"]] == [
        (1000.0, 2, 2), (1002.0, 1, 1)]
    assert indices["buckets"][0]["values"] == {"sensitivity": 0.0, "agent": 0.1, "error": 0.9}
    assert [(b["start"], b["rows"]) for b in wide["buckets"]] == [(960.0, 3)]


def test_nothing_is_asked_while_the_annotate_switch_is_off(tmp_path):
    directory = write_session(tmp_path, [["key_press"], []])
    jev = FakeJev()
    client = client_for(tmp_path, jev)
    client.patch("/control/server", json={"annotate": False})
    with client:
        time.sleep(0.2)
        assert jev.asked == []
        client.patch("/control/server", json={"annotate": True})
        wait_for_answers(tmp_path, 1)


def test_a_row_jev_refuses_is_stored_with_its_error(tmp_path):
    directory = write_session(tmp_path, [["key_press"], []])

    async def refuse(state, questions):
        raise TypeSafeBadRequestError(400, {"detail": "bad image"}, Headers())

    with client_for(tmp_path, refuse) as client:
        wait_for_answers(tmp_path, 1)
        events = client.get("/events").json()["data"]
    record = FileQuestionAnswers(devices_dir(tmp_path)).get(SESSION, 0)
    assert all(row.error and not row.answers for row in record.rows.values())
    assert all(e["indices"] == {} and e["moments"] == [] for e in events)

