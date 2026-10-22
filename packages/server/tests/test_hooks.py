"""Prompts Claude Code reports through its hook: which are kept, and the event whose moment carries each."""

import time

import pytest

from test_events import DESKTOP, LAPTOP, client_for, put
from keypoints.derive import Moment
from server.hooks import merge_moments

TERMINAL = {"X-Claude-Code-Entrypoint": "cli"}


def prompt(client, device="laptop", headers=TERMINAL, **fields):
    body = {"hook_event_name": "UserPromptSubmit", "session_id": "conversation-1",
            "cwd": "/work/project", "prompt_id": "p1", "prompt": "a question",
            "transcript_path": "/x.jsonl", "permission_mode": "default", **fields}
    response = client.post("/hooks/claude-code", params={"device": device}, json=body,
                           headers=headers)
    assert response.status_code == 204 and response.content == b""


def moments(client):
    return {e["id"]: e["moments"] for e in client.get("/events", params={"order": "asc"}).json()["data"]}


def test_a_prompt_typed_in_the_terminal_is_a_moment_on_the_event_it_was_sent_in(tmp_path):
    now = time.time()
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, now - 5)
        put(client, LAPTOP, 1, now + 60)
        put(client, DESKTOP, 0, now - 5)
        prompt(client)
        found = moments(client)

    assert found[f"{LAPTOP}-000000"] == [{
        "type": "agent_prompt_submitted", "probability": 1.0, "detector": "claude_code_hook",
        "label": "claude_code",
        "conversation": {"harness": "claude_code", "session_id": "conversation-1", "cwd": "/work/project"}}]
    assert found[f"{LAPTOP}-000001"] == []
    # Another device's event at the same time has no moment.
    assert found[f"{DESKTOP}-000000"] == []


def test_a_prompt_while_nothing_was_recorded_is_on_no_event(tmp_path):
    now = time.time()
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, now - 100)
        prompt(client)
        assert moments(client) == {f"{LAPTOP}-000000": []}


@pytest.mark.parametrize("headers, fields", [
    ({"X-Claude-Code-Entrypoint": "cli", "X-Claude-Code-Remote": "true"}, {}),
    ({"X-Claude-Code-Entrypoint": "claude-desktop"}, {}),
    ({"X-Claude-Code-Entrypoint": "sdk-cli"}, {}),
    ({}, {}),
    (TERMINAL, {"agent_id": "subagent-1"}),
    (TERMINAL, {"hook_event_name": "Stop"}),
], ids=["cloud", "desktop app", "claude -p", "no entrypoint", "subagent", "not a prompt"])
def test_a_prompt_that_cannot_be_resumed_in_a_terminal_is_answered_but_not_kept(tmp_path, headers, fields):
    now = time.time()
    with client_for(tmp_path) as client:
        put(client, LAPTOP, 0, now - 5)
        put(client, LAPTOP, 1, now + 60)
        prompt(client, headers=headers, **fields)
        assert all(m == [] for m in moments(client).values())


def test_a_device_id_that_is_not_one_is_refused(tmp_path):
    with client_for(tmp_path) as client:
        response = client.post("/hooks/claude-code", params={"device": "../x"}, headers=TERMINAL,
                               json={"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": "/"})
    assert response.status_code == 422


def test_a_reported_prompt_stands_in_for_jevs_guess_on_its_row():
    guess = Moment(type="agent_prompt_submitted", probability=0.8, detector="jev")
    error = Moment(type="error_shown", probability=0.9, detector="jev")
    reported = Moment(type="agent_prompt_submitted", probability=1.0, detector="claude_code_hook")
    merged = merge_moments({0: [guess, error], 1: [guess]}, {0: [reported]})
    assert merged == {0: [error, reported], 1: [guess]}
