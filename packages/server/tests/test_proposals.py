"""Proposals: a day run through Dawn is kept with every task and call; a proposal's state is read from its trail."""

import asyncio
import json
from typing import Optional

from annotate.store import FileActivityCaptions
from juno.config import DawnConfig, LatticeConfig, devices_dir
from lattice.llm import Reply
from lattice.proposals import (Decision, FileProposals, ModelCallRecord, bucket_of, day_bounds, new_run,
                               parse_proposal_id, proposal_id, propose_run, propose_task, proposals_dir, state_of)
from lattice.store import FileLattices, lattice_dir
from test_dawn import caption
from test_lattice_routes import build_record, stored_lattice

DAY = "2026-01-05"
SESSION = "laptop-session_20260105_090000"


def fake_call(replies: dict[str, object], seen: Optional[list[str]] = None):
    """Answers each prompt by the first marker found in it."""

    async def call(model: str, prompt: str, schema: Optional[dict]) -> Reply:
        if seen is not None:
            seen.append(prompt)
        for marker, reply in replies.items():
            if marker in prompt:
                return Reply(text=json.dumps(reply), prompt_tokens=10, completion_tokens=3)
        raise AssertionError(f"no reply for prompt starting {prompt[:60]!r}")

    return call


# A day of two tasks: "Writing" scores over the line, "Sorting" under it; every insight ranked by the model.
REPLIES = {
    "[IMPLEMENTATION CONSTRAINTS]": {
        "reframings": ["HMW make drafting feel safe?"],
        "actions": [{"title": "Outline", "action": "Draft an outline.", "insight_ids": [0], "benefit": "Lowers the bar."},
                    {"title": "Timebox", "action": "Add a block to the calendar.", "insight_ids": [0]}],
        "considered": [{"title": "Rewrite it", "why": "needs the user's judgement"}]},
    "[INSIGHT]\n": {"relevance": 0.6},
    "[TASK]\nWriting": {"score": 0.9, "reasoning": "deep"},
    "[TASK]\nSorting": {"score": 0.7, "reasoning": "generic"},
    "list of actions": {"tasks": [{"task": "Writing a paper", "reasoning": "r1"}, {"task": "Sorting a list", "reasoning": "r2"}]},
}


def stores(tmp_path):
    captions = FileActivityCaptions(devices_dir(tmp_path))
    start = day_bounds(DAY)[0] + 3600
    captions.save(caption(SESSION, 0, start, ["Typed a title.", "Saved the file."]))
    captions.save(caption(SESSION, 1, start + 600, ["Opened the report."]))
    lattices = FileLattices(lattice_dir(tmp_path))
    lattices.save(stored_lattice(build_record()))
    return captions, lattices, FileProposals(proposals_dir(tmp_path))


def config() -> DawnConfig:
    return DawnConfig(rerank_model="llm", actions_per_window=0, utility_threshold=0.75, insights_per_task=1)


def test_a_run_keeps_every_task_its_ranking_and_the_calls(tmp_path):
    captions, lattices, proposals = stores(tmp_path)
    run = new_run(config(), "Ada", DAY, build_record().id, None)
    seen = []
    done = asyncio.run(propose_run(run, captions, lattices, proposals, config(), LatticeConfig(), fake_call(REPLIES, seen)))
    assert done.status == "done" and done.actions == 3 and done.insights == 1
    assert [(s.session_id, s.actions) for s in done.sessions] == [(SESSION, 3)]
    writing, sorting = done.tasks
    assert (writing.task, writing.score, writing.asked, writing.asked_by) == ("Writing a paper", 0.9, True, "threshold")
    assert (sorting.task, sorting.score, sorting.asked, sorting.asked_by) == ("Sorting a list", 0.7, False, None)
    assert [(r.id, r.score, r.given) for r in writing.ranked] == [(0, 0.6, True)]
    assert [a.title for a in writing.actions] == ["Outline", "Timebox"]
    assert [c.title for c in writing.considered] == ["Rewrite it"] and sorting.actions == []
    assert proposals.get_run(run.id) == done and [r.id for r in proposals.runs()] == [run.id]
    calls = proposals.calls(run.id)
    assert [(c.step, c.what) for c in calls] == [
        ("tasks", "window 1 of 1"), ("utility", "task 1"), ("utility", "task 2"),
        ("rerank", "call 1"), ("rerank", "task 1"), ("rerank", "call 2"), ("rerank", "task 2"), ("actions", "task 1")]
    assert calls[-1].prompt_tokens == 10 and "[IMPLEMENTATION CONSTRAINTS]" in calls[-1].prompt
    assert json.loads(calls[-1].reply)["actions"][0]["title"] == "Outline"
    assert all(c.seconds >= 0 for c in calls)


def test_a_task_under_the_line_can_be_proposed_for_by_the_user(tmp_path):
    captions, lattices, proposals = stores(tmp_path)
    run = asyncio.run(propose_run(new_run(config(), "Ada", DAY, build_record().id, None), captions, lattices, proposals,
                                  config(), LatticeConfig(), fake_call(REPLIES)))
    again = asyncio.run(propose_task(run, 1, lattices, proposals, config(), LatticeConfig(), fake_call(REPLIES)))
    sorting = again.tasks[1]
    assert sorting.asked and sorting.asked_by == "user" and [a.title for a in sorting.actions] == ["Outline", "Timebox"]
    assert proposals.get_run(run.id).tasks[1].asked
    assert proposals.calls(run.id)[-1].what == "task 2, asked by the user"


def test_a_run_over_nothing_fails_and_says_so(tmp_path):
    captions, lattices, proposals = stores(tmp_path)
    run = asyncio.run(propose_run(new_run(config(), "Ada", "2026-01-06", build_record().id, None), captions, lattices,
                                  proposals, config(), LatticeConfig(), fake_call(REPLIES)))
    assert run.status == "failed" and "no action captions" in run.error
    assert proposals.get_run(run.id).status == "failed"


def test_a_failed_model_call_costs_only_its_task(tmp_path):
    captions, lattices, proposals = stores(tmp_path)
    replies = dict(REPLIES)
    del replies["[TASK]\nSorting"]
    run = asyncio.run(propose_run(new_run(config(), "Ada", DAY, build_record().id, None), captions, lattices, proposals,
                                  config(), LatticeConfig(), fake_call(replies)))
    assert run.status == "done"
    assert run.tasks[0].asked and run.tasks[1].score is None and "AssertionError" in run.tasks[1].error
    assert [c.error is not None for c in proposals.calls(run.id) if c.what == "task 2" and c.step == "utility"] == [True]


def test_ids_name_the_run_task_and_action():
    assert proposal_id("dawn_20260105_050000", 3, 1) == "dawn_20260105_050000-03-1"
    assert parse_proposal_id("dawn_20260105_050000-03-1") == ("dawn_20260105_050000", 3, 1)
    assert parse_proposal_id("dawn_20260105_050000-3-1") is None


def decision(event: str, at: float, **rest) -> Decision:
    return Decision(event=event, at=at, where="desktop", **rest)


def test_a_proposals_state_is_read_from_its_trail():
    assert state_of([], 30).state == "new"
    listed = [decision("listed", 1.0)]
    assert state_of(listed, 30).state == "new" and bucket_of(state_of(listed, 30)) == "open"
    seen = listed + [decision("opened", 2.0, dwell=12)]
    assert (state_of(seen, 30).state, state_of(seen, 30).dwell) == ("seen", 12.0)
    quick_no = seen + [decision("dismissed", 3.0)]
    assert (state_of(quick_no, 30).state, state_of(quick_no, 30).almost, bucket_of(state_of(quick_no, 30))) == ("dismissed", False, "dismissed")
    read_no = listed + [decision("opened", 2.0, dwell=40), decision("snoozed", 3.0, detail="after the table")]
    assert (state_of(read_no, 30).state, state_of(read_no, 30).almost, bucket_of(state_of(read_no, 30))) == ("snoozed", True, "almost")
    assert state_of(read_no, 30).decided.detail == "after the table"
    edited_no = seen + [decision("edited", 2.5, detail="only last week"), decision("dismissed", 3.0)]
    assert state_of(edited_no, 30).almost and state_of(edited_no, 30).edited
    yes = listed + [decision("opened", 2.0, dwell=100), decision("accepted", 3.0)]
    assert (state_of(yes, 30).state, state_of(yes, 30).almost, bucket_of(state_of(yes, 30))) == ("accepted", False, "accepted")
    reopened = quick_no + [decision("reopened", 4.0)]
    assert state_of(reopened, 30).state == "seen" and state_of(reopened, 30).decided is None


def test_trails_and_calls_are_appended_per_run(tmp_path):
    proposals = FileProposals(proposals_dir(tmp_path))
    run_id = "dawn_20260105_050000"
    assert proposals.trails(run_id) == {} and proposals.calls(run_id) == []
    proposals.add_decision(run_id, f"{run_id}-00-0", decision("listed", 1.0))
    proposals.add_decision(run_id, f"{run_id}-00-1", decision("listed", 1.0))
    proposals.add_decision(run_id, f"{run_id}-00-0", decision("accepted", 2.0))
    trails = proposals.trails(run_id)
    assert [d.event for d in trails[f"{run_id}-00-0"]] == ["listed", "accepted"]
    assert [d.event for d in trails[f"{run_id}-00-1"]] == ["listed"]
    proposals.add_call(run_id, ModelCallRecord(step="tasks", what="window 1 of 1", seconds=0.5))
    assert [c.what for c in proposals.calls(run_id)] == ["window 1 of 1"]
