"""Dawn's steps over a lattice: a day's captions become tasks, the tasks are scored and filtered,
and each kept task gets two actions steered by its best-ranked insights."""

import asyncio
import json
from typing import Optional

import pytest

from annotate.label import Action, Annotation
from annotate.request import AnnotationFrame
from annotate.sanitize import Sanitization
from annotate.store import ActivityCaption, new_caption
from lattice.dawn import (INDUCE_TASKS_PROMPT, day_actions, induce_tasks, insight_document, keep,
                          propose_actions, score_utility, windows)
from lattice.llm import Reply
from lattice.models import Insight, NodeMetadata
from lattice.rerank import ModelReranker, reranker_for


def caption(session_id: str, chunk: int, start: float, actions: list[str]) -> ActivityCaption:
    frames = [AnnotationFrame(label=f"Frame {n}", screenshot_path="x.png", timestamp=start + n,
                              cursor=None, monitor=None, events="") for n in range(1, len(actions) + 1)]
    record = new_caption(session_id, chunk, "fake/model")
    return record.model_copy(update={
        "status": "done", "frames": frames, "start_time": start, "end_time": start + len(actions),
        "annotation": Annotation(actions=[Action(start=n, end=n, caption=text)
                                          for n, text in enumerate(actions, start=1)],
                                 dense_caption="screen",
                                 sanitization=Sanitization(algorithm="none", version=1, text=False, images=False, redactions={}))})


def insight(id: int, title: str) -> Insight:
    return Insight(id=id, title=title, tagline=f"{title} tagline", insight=f"{title} insight",
                   context="at work", metadata=NodeMetadata(input_session=0))


def fake_call(replies: dict[str, object], seen: list[str]):
    """A ``ModelCall`` answering by which prompt it is sent; records the prompts."""

    async def call(model: str, prompt: str, schema: Optional[dict]) -> Reply:
        seen.append(prompt)
        for marker, reply in replies.items():
            if marker in prompt:
                return Reply(text=json.dumps(reply) if not isinstance(reply, str) else reply)
        raise AssertionError(f"no reply for prompt starting {prompt[:60]!r}")

    return call


def test_a_days_actions_are_listed_in_time_order_with_the_clock_and_only_from_done_captions():
    later = caption("dev-s1", 1, 2000.0, ["Opened the report."])
    earlier = caption("dev-s1", 0, 1000.0, ["Typed a title.", "Saved the file."])
    pending = caption("dev-s1", 2, 3000.0, ["Never shown."]).model_copy(update={"status": "pending"})
    lines = day_actions([later, pending, earlier])
    assert [line.split(" | ", 1)[1] for line in lines] == ["Typed a title.", "Saved the file.", "Opened the report."]
    assert all(len(line.split(" | ", 1)[0]) == 5 for line in lines)


def test_windows_cut_the_day_or_keep_it_whole():
    assert windows(["a", "b", "c"], 2) == [["a", "b"], ["c"]]
    assert windows(["a", "b", "c"], 0) == [["a", "b", "c"]]


def test_tasks_of_each_window_are_merged_by_a_second_pass_over_the_goals():
    seen = []
    replies = {"- 09:00 | first": {"tasks": [{"task": "Writing the paper", "reasoning": "r"}]},
               "- 10:00 | second": {"tasks": [{"task": "Planning the trip", "reasoning": "r"}]},
               "- Writing the paper": {"tasks": [{"task": "Writing the paper", "reasoning": "merged"}]}}
    tasks = asyncio.run(induce_tasks(fake_call(replies, seen), "m", "Ada",
                                     ["09:00 | first", "10:00 | second"], actions_per_window=1))
    assert [t.task for t in tasks] == ["Writing the paper"]
    assert len(seen) == 3 and seen[0].startswith(INDUCE_TASKS_PROMPT[:40].format(user_name="Ada")[:20])
    assert "- Writing the paper\n- Planning the trip" in seen[2]


def test_one_window_needs_no_merge():
    seen = []
    tasks = asyncio.run(induce_tasks(fake_call({"- 09:00": {"tasks": [{"task": "T", "reasoning": ""}]}}, seen),
                                     "m", "Ada", ["09:00 | a", "09:01 | b"], actions_per_window=0))
    assert [t.task for t in tasks] == ["T"] and len(seen) == 1


def test_tasks_under_the_threshold_are_dropped_and_scoring_sees_every_insight():
    seen = []
    call = fake_call({"[TASK]\nWriting": {"score": 0.9, "reasoning": "deep"},
                      "[TASK]\nSorting": {"score": 0.2, "reasoning": "generic"}}, seen)
    insights = [insight(0, "Perfectionism"), insight(1, "Avoidance")]
    scored = [asyncio.run(score_utility(call, "m", task, insights))
              for task in induce_stub(["Writing a paper", "Sorting a list"])]
    assert [t.score for t in scored] == [0.9, 0.2]
    assert [t.task for t in keep(scored, 0.75)] == ["Writing a paper"]
    assert "ID: 0 | Perfectionism" in seen[0] and "ID: 1 | Avoidance" in seen[0]


def induce_stub(names: list[str]):
    from lattice.dawn import TaskReply
    return [TaskReply(task=name, reasoning="") for name in names]


def test_the_llm_reranker_keeps_the_best_scored_insights_in_order():
    call = fake_call({"[INSIGHT]\nAvoidance": {"relevance": 0.9}, "[INSIGHT]\nPerfectionism": {"relevance": 0.4},
                      "[INSIGHT]\nTidiness": {"relevance": 0.6}}, [])
    docs = [insight_document(insight(i, name)) for i, name in enumerate(["Perfectionism", "Avoidance", "Tidiness"])]
    ranked = asyncio.run(ModelReranker(call, "m").rank("Writing", docs, 2))
    assert [r.index for r in ranked] == [1, 2]
    assert isinstance(reranker_for("llm", call, "m"), ModelReranker)
    with pytest.raises(ValueError):
        reranker_for("cohere/x", call, "m")


def test_actions_are_proposed_from_the_task_its_insights_and_the_constraints():
    seen = []
    call = fake_call({"[IMPLEMENTATION CONSTRAINTS]": {
        "reframings": ["HMW make drafting feel safe?"],
        "actions": [{"title": "Outline", "action": "Draft an outline in Google Drive.", "insight_ids": [1],
                     "benefit": "Lowers the bar."},
                    {"title": "Timebox", "action": "Add a 25-minute block to Apple Calendar.", "insight_ids": [1]}],
        "considered": [{"title": "Rewrite the paper", "why": "not implementable: needs the user's judgement"}]}}, seen)
    [task] = keep([asyncio.run(score_utility(fake_call({"[TASK]": {"score": 1.0}}, []), "m",
                                             induce_stub(["Writing a paper"])[0], []))], 0.75)
    proposed = asyncio.run(propose_actions(call, "m", "Ada", task, [insight(1, "Avoidance")],
                                           "- Access MCP servers for Apple Calendar"))
    assert proposed.insight_ids == [1] and len(proposed.actions) == 2
    assert proposed.actions[0].title == "Outline" and proposed.reframings == ["HMW make drafting feel safe?"]
    assert [(c.title, c.why) for c in proposed.considered] == [("Rewrite the paper", "not implementable: needs the user's judgement")]
    assert '"considered"' in seen[-1]
    assert "Access MCP servers for Apple Calendar" in seen[-1] and "ID: 1 | Avoidance" in seen[-1]
