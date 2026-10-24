"""Proposals: Dawn's actions for one day, what the user did with each, and where both are kept.

A run (``ProposalRun``) is one day proposed over, steered by one lattice: the
tasks induced from the day's action captions, each with its utility score and
every top-layer insight ranked for it, and, for the tasks asked (at or over the
threshold, or by the user), the proposer's reframings, the candidates it cut
and its two actions. A proposal is one of those actions, named
``<run id>-<task>-<action>``.

What the user did with a proposal is its trail: ``Decision`` lines, one per
event, appended to the run's ``decisions.jsonl`` and never rewritten. Its
state (``state_of``) is read from the trail each time: the last decision, else
seen once opened, else new; and almost when a no came after an edit or after
``almost_seconds`` of reading. ``propose_run`` runs the day; ``propose_task``
runs the proposer for one task under the line.
"""

from __future__ import annotations

import datetime
import logging
import re
import time
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol

from pydantic import BaseModel, ValidationError

from annotate.store import ActivityCaption, ActivityCaptions
from juno.config import DawnConfig, LatticeConfig
from juno.sessions import replace_file
from lattice.dawn import (ActionReply, Considered, ScoredTask, TaskReply, day_actions, induce_tasks,
                          insight_document, propose_actions, score_utility, windows)
from lattice.llm import ModelCall, Reply, litellm_call
from lattice.models import Insight, Lattice
from lattice.rerank import Ranked, Reranker, reranker_for
from lattice.store import Lattices

logger = logging.getLogger(__name__)

PROPOSALS_DIR = "proposals"
RUN_FILE = "run.json"
DECISIONS_FILE = "decisions.jsonl"
CALLS_FILE = "calls.jsonl"
RUN_ID_PATTERN = r"dawn_\d{8}_\d{6}"
PROPOSAL_ID_PATTERN = rf"{RUN_ID_PATTERN}-\d{{2}}-\d"
RUN_ID = re.compile(f"^{RUN_ID_PATTERN}$")
PROPOSAL_ID = re.compile(rf"^(?P<run>{RUN_ID_PATTERN})-(?P<task>\d{{2}})-(?P<action>\d)$")
DAY_PATTERN = r"\d{4}-\d{2}-\d{2}"

RunStatus = Literal["running", "done", "failed"]
AskedBy = Literal["threshold", "user"]
DecisionEvent = Literal["listed", "opened", "expanded", "edited", "accepted", "dismissed", "snoozed", "reopened"]
DECIDING: tuple[str, ...] = ("accepted", "dismissed", "snoozed")
State = Literal["new", "seen", "accepted", "dismissed", "snoozed"]


class Decision(BaseModel):
    """One event of a proposal's trail."""

    event: DecisionEvent
    # Unix seconds, the server's clock.
    at: float
    # The client it came from: desktop, phone, or the server for ``listed``.
    where: str
    # The section expanded, the draft after an edit, the note given with a decision.
    detail: Optional[str] = None
    # Seconds the proposal was open, sent with ``opened`` when the page is left.
    dwell: Optional[float] = None


class DecisionLine(Decision):
    """One line of ``decisions.jsonl``: a decision and the proposal it is about."""

    proposal: str


class ProposalState(BaseModel):
    """What a trail says about a proposal now."""

    state: State
    # A no (dismissed or snoozed) after the action was edited or read for ``almost_seconds``.
    almost: bool
    # Seconds read, over every opening.
    dwell: float
    edited: bool
    # The last decision, when there is one.
    decided: Optional[Decision] = None


def state_of(trail: list[Decision], almost_seconds: float) -> ProposalState:
    """The state the trail puts the proposal in; ``reopened`` undoes the decisions before it."""
    since_reopen = trail
    for n in range(len(trail) - 1, -1, -1):
        if trail[n].event == "reopened":
            since_reopen = trail[n + 1:]
            break
    decided = next((d for d in reversed(since_reopen) if d.event in DECIDING), None)
    dwell = sum(d.dwell or 0.0 for d in trail if d.event == "opened")
    edited = any(d.event == "edited" for d in since_reopen)
    opened = any(d.event == "opened" for d in trail)
    state: State = decided.event if decided else "seen" if opened else "new"  # type: ignore[assignment]
    almost = decided is not None and decided.event != "accepted" and (edited or dwell >= almost_seconds)
    return ProposalState(state=state, almost=almost, dwell=dwell, edited=edited, decided=decided)


Bucket = Literal["open", "accepted", "almost", "dismissed"]


def bucket_of(state: ProposalState) -> Bucket:
    """The one list a proposal is in: open until decided; a no is almost or dismissed."""
    if state.almost:
        return "almost"
    if state.state == "accepted":
        return "accepted"
    return "dismissed" if state.decided else "open"


class ModelCallRecord(BaseModel):
    """One model call of a run: which step, what for, how long, what was sent and came back."""

    step: Literal["tasks", "utility", "rerank", "actions"]
    what: str
    seconds: float
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    prompt: Optional[str] = None
    reply: Optional[str] = None
    error: Optional[str] = None


class RankedInsight(BaseModel):
    """A top-layer insight scored against a task by the reranker."""

    id: int
    score: float
    # Whether it was among the ``insights_per_task`` the proposer was given.
    given: bool


class ProposalTask(BaseModel):
    """One task of the day, as far as the run took it."""

    index: int
    task: str
    reasoning: str = ""
    # ``UtilityReply.score``; None when the call failed.
    score: Optional[float]
    utility_reasoning: str = ""
    asked: bool = False
    asked_by: Optional[AskedBy] = None
    # Every top-layer insight, best first.
    ranked: list[RankedInsight] = []
    reframings: list[str] = []
    considered: list[Considered] = []
    actions: list[ActionReply] = []
    error: Optional[str] = None


class RunSession(BaseModel):
    """A recorder session the day's captions came from."""

    session_id: str
    device_id: str
    # Unix seconds of the first and last captioned frame.
    start: float
    end: float
    actions: int


class ProposalRun(BaseModel):
    """One day proposed over, written when it starts and updated as it goes."""

    id: str
    # Local calendar day, YYYY-MM-DD.
    day: str
    device_id: Optional[str]
    build_id: str
    status: RunStatus
    created_at: float
    updated_at: float
    finished_at: Optional[float] = None
    seconds: Optional[float] = None
    user_name: str
    # Role -> litellm model string: task, utility, action, rerank.
    models: dict[str, str]
    utility_threshold: float
    insights_per_task: int
    actions_per_window: int
    # Action captions read, and insights in the lattice's top layer.
    actions: int = 0
    insights: int = 0
    sessions: list[RunSession] = []
    tasks: list[ProposalTask] = []
    error: Optional[str] = None


def new_run_id(now: Optional[float] = None) -> str:
    return time.strftime("dawn_%Y%m%d_%H%M%S", time.localtime(time.time() if now is None else now))


def proposal_id(run_id: str, task: int, action: int) -> str:
    return f"{run_id}-{task:02d}-{action}"


def parse_proposal_id(value: str) -> Optional[tuple[str, int, int]]:
    """``(run id, task index, action index)`` for a proposal id, or None if it is not one."""
    match = PROPOSAL_ID.match(value)
    return (match.group("run"), int(match.group("task")), int(match.group("action"))) if match else None


def proposals_dir(data_dir: Path) -> Path:
    return data_dir / PROPOSALS_DIR


def day_bounds(day: str) -> tuple[float, float]:
    """Unix seconds of the local day's start and end."""
    start = datetime.datetime.strptime(day, "%Y-%m-%d").astimezone()
    return start.timestamp(), (start + datetime.timedelta(days=1)).timestamp()


def new_run(config: DawnConfig, user_name: str, day: str, build_id: str, device_id: Optional[str],
            now: Optional[float] = None) -> ProposalRun:
    """A run record for ``day`` with a fresh id, before any caption is read."""
    now = time.time() if now is None else now
    return ProposalRun(id=new_run_id(now), day=day, device_id=device_id, build_id=build_id, status="running",
                       created_at=now, updated_at=now, user_name=user_name,
                       models={"task": config.task_model, "utility": config.utility_model,
                               "action": config.action_model, "rerank": config.rerank_model},
                       utility_threshold=config.utility_threshold, insights_per_task=config.insights_per_task,
                       actions_per_window=config.actions_per_window)


class Proposals(Protocol):

    def get_run(self, run_id: str) -> Optional[ProposalRun]:
        """The run, or None if there is none."""

    def save_run(self, run: ProposalRun) -> None:
        """Store the run, replacing its earlier record."""

    def runs(self) -> list[ProposalRun]:
        """Every run, newest first."""

    def trails(self, run_id: str) -> dict[str, list[Decision]]:
        """Every proposal's trail in the run, by proposal id, in the order the events came."""

    def add_decision(self, run_id: str, proposal: str, decision: Decision) -> None:
        """Append one event to a proposal's trail."""

    def calls(self, run_id: str) -> list[ModelCallRecord]:
        """The run's model calls, in order."""

    def add_call(self, run_id: str, record: ModelCallRecord) -> None:
        """Append one model call to the run's record."""


class FileProposals:
    """Run ``<id>`` under ``<directory>/<id>/``: ``run.json``, ``decisions.jsonl``, ``calls.jsonl``."""

    def __init__(self, directory: Path):
        self.directory = directory

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise ValueError(f"not a run id: {run_id!r}")
        return self.directory / run_id

    def get_run(self, run_id: str) -> Optional[ProposalRun]:
        try:
            return ProposalRun.model_validate_json((self.run_dir(run_id) / RUN_FILE).read_text())
        except FileNotFoundError:
            return None

    def save_run(self, run: ProposalRun) -> None:
        replace_file(self.run_dir(run.id) / RUN_FILE, run.model_dump_json(indent=2))

    def runs(self) -> list[ProposalRun]:
        runs = []
        if not self.directory.is_dir():
            return runs
        for path in self.directory.glob(f"*/{RUN_FILE}"):
            try:
                runs.append(ProposalRun.model_validate_json(path.read_text()))
            except (OSError, ValidationError) as exc:
                logger.warning("skipping %s: %s", path, exc)
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def lines(self, run_id: str, name: str, model: type[BaseModel]) -> list[BaseModel]:
        try:
            text = (self.run_dir(run_id) / name).read_text()
        except FileNotFoundError:
            return []
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(model.model_validate_json(line))
            except ValidationError as exc:
                logger.warning("skipping %s line %d of %s: %s", name, number, run_id, exc)
        return rows

    def append(self, run_id: str, name: str, row: BaseModel) -> None:
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as file:
            file.write(row.model_dump_json() + "\n")

    def trails(self, run_id: str) -> dict[str, list[Decision]]:
        trails: dict[str, list[Decision]] = {}
        for line in self.lines(run_id, DECISIONS_FILE, DecisionLine):
            trails.setdefault(line.proposal, []).append(Decision(**line.model_dump(exclude={"proposal"})))
        return trails

    def add_decision(self, run_id: str, proposal: str, decision: Decision) -> None:
        self.append(run_id, DECISIONS_FILE, DecisionLine(proposal=proposal, **decision.model_dump()))

    def calls(self, run_id: str) -> list[ModelCallRecord]:
        return list(self.lines(run_id, CALLS_FILE, ModelCallRecord))

    def add_call(self, run_id: str, record: ModelCallRecord) -> None:
        self.append(run_id, CALLS_FILE, record)


def top_insights(lattice: Lattice) -> list[Insight]:
    """The lattice's output: the insights of its top layer."""
    return list(lattice.layers[-1].insights) if lattice.layers else []


def run_sessions(captions: list[ActivityCaption]) -> list[RunSession]:
    """The recorder sessions the day's done captions came from, with their span and action count."""
    sessions: dict[str, RunSession] = {}
    for caption in captions:
        if caption.status != "done" or caption.annotation is None or caption.start_time is None:
            continue
        end = caption.end_time if caption.end_time is not None else caption.start_time
        found = sessions.get(caption.session_id)
        if found is None:
            sessions[caption.session_id] = RunSession(session_id=caption.session_id, device_id=caption.device_id,
                                                      start=caption.start_time, end=end,
                                                      actions=len(caption.annotation.actions))
        else:
            found.start = min(found.start, caption.start_time)
            found.end = max(found.end, end)
            found.actions += len(caption.annotation.actions)
    return sorted(sessions.values(), key=lambda s: s.start)


def recorded(call: ModelCall, record: Callable[[ModelCallRecord], None], step: str,
             label: Callable[[int], str]) -> ModelCall:
    """``call`` with each call recorded under ``step``, named by ``label`` of its number from 1."""
    count = 0

    async def logged(model: str, prompt: str, schema: Optional[dict]) -> Reply:
        nonlocal count
        count += 1
        what = label(count)
        started = time.time()
        try:
            reply = await call(model, prompt, schema)
        except Exception as exc:
            record(ModelCallRecord(step=step, what=what, seconds=time.time() - started, prompt=prompt,
                                   error=f"{type(exc).__name__}: {exc}"))
            raise
        record(ModelCallRecord(step=step, what=what, seconds=time.time() - started,
                               prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
                               prompt=prompt, reply=reply.text))
        return reply

    return logged


async def rank_insights(reranker: Reranker, task: str, insights: list[Insight], given: int,
                        record: Callable[[ModelCallRecord], None], what: str) -> list[RankedInsight]:
    """Every insight scored against ``task``, best first, the first ``given`` marked given."""
    started = time.time()
    ranked: list[Ranked] = await reranker.rank(task, [insight_document(i) for i in insights], len(insights))
    record(ModelCallRecord(step="rerank", what=what, seconds=time.time() - started))
    return [RankedInsight(id=insights[r.index].id, score=r.score, given=n < given) for n, r in enumerate(ranked)]


def given_insights(task: ProposalTask, insights: list[Insight]) -> list[Insight]:
    """The insights the proposer is given for ``task``, best ranked first."""
    by_id = {i.id: i for i in insights}
    return [by_id[r.id] for r in task.ranked if r.given and r.id in by_id]


async def propose_for(task: ProposalTask, run: ProposalRun, insights: list[Insight], call: ModelCall,
                      config: DawnConfig, asked_by: AskedBy) -> None:
    """The proposer over ``task``; its actions, reframings and cut candidates go on the task."""
    scored = ScoredTask(task=task.task, reasoning=task.reasoning, score=task.score,
                        utility_reasoning=task.utility_reasoning)
    task.asked, task.asked_by, task.error = True, asked_by, None
    try:
        proposed = await propose_actions(call, config.action_model, run.user_name, scored,
                                         given_insights(task, insights), config.constraints)
    except Exception as exc:
        logger.warning("proposing for %r failed: %s", task.task, exc)
        task.error = f"{type(exc).__name__}: {exc}"
        return
    task.reframings, task.considered, task.actions = proposed.reframings, proposed.considered, proposed.actions


def call_for(endpoint: LatticeConfig, call: Optional[ModelCall]) -> ModelCall:
    return call if call is not None else litellm_call(endpoint)


async def propose_run(run: ProposalRun, captions: ActivityCaptions, lattices: Lattices, proposals: Proposals,
                      config: DawnConfig, endpoint: LatticeConfig, call: Optional[ModelCall] = None,
                      reranker: Optional[Reranker] = None) -> ProposalRun:
    """Dawn over the run's day, saving the run as each step ends; ``call`` replaces the models' endpoint."""
    started = time.time()
    proposals.save_run(run)
    try:
        lattice = lattices.get(run.build_id)
        if lattice is None:
            raise ValueError(f"build {run.build_id} has no lattice")
        insights = top_insights(lattice)
        since, until = day_bounds(run.day)
        day_captions = captions.all(device_id=run.device_id, since=since, until=until)
        actions = day_actions(day_captions)
        run.sessions, run.actions, run.insights = run_sessions(day_captions), len(actions), len(insights)
        if not actions or not insights:
            raise ValueError("nothing to work from: the day has no action captions or the lattice no insights")
        def record(entry: ModelCallRecord) -> None:
            proposals.add_call(run.id, entry)

        base = call_for(endpoint, call)
        parts = len(windows(actions, config.actions_per_window))
        tasks_call = recorded(base, record, "tasks",
                              lambda n: f"window {n} of {parts}" if n <= parts else f"merge of {parts} windows")
        induced: list[TaskReply] = await induce_tasks(tasks_call, config.task_model, run.user_name, actions,
                                                      config.actions_per_window)
        run.tasks = [ProposalTask(index=n, task=t.task, reasoning=t.reasoning, score=None) for n, t in enumerate(induced)]
        proposals.save_run(run)
        for task, reply in zip(run.tasks, induced):
            utility_call = recorded(base, record, "utility", lambda n, i=task.index: f"task {i + 1}")
            try:
                scored = await score_utility(utility_call, config.utility_model, reply, insights)
                task.score, task.utility_reasoning = scored.score, scored.utility_reasoning
            except Exception as exc:
                logger.warning("scoring %r failed: %s", task.task, exc)
                task.error = f"{type(exc).__name__}: {exc}"
        proposals.save_run(run)
        ranker = reranker if reranker is not None else reranker_for(
            config.rerank_model, recorded(base, record, "rerank", lambda n: f"call {n}"), config.utility_model)
        for task in run.tasks:
            task.ranked = await rank_insights(ranker, task.task, insights, config.insights_per_task, record,
                                              f"task {task.index + 1}")
        proposals.save_run(run)
        for task in run.tasks:
            if task.score is not None and task.score >= config.utility_threshold:
                actions_call = recorded(base, record, "actions", lambda n, i=task.index: f"task {i + 1}")
                await propose_for(task, run, insights, actions_call, config, "threshold")
                proposals.save_run(run)
        run.status = "done"
    except Exception as exc:
        logger.exception("proposal run %s failed", run.id)
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
    run.seconds = time.time() - started
    run.finished_at = run.updated_at = time.time()
    proposals.save_run(run)
    return run


async def propose_task(run: ProposalRun, index: int, lattices: Lattices, proposals: Proposals, config: DawnConfig,
                       endpoint: LatticeConfig, call: Optional[ModelCall] = None) -> ProposalRun:
    """The proposer over one task of a finished run, asked for by the user; the run is saved."""
    lattice = lattices.get(run.build_id)
    if lattice is None:
        raise ValueError(f"build {run.build_id} has no lattice")
    task = run.tasks[index]

    def record(entry: ModelCallRecord) -> None:
        proposals.add_call(run.id, entry)

    actions_call = recorded(call_for(endpoint, call), record, "actions", lambda n: f"task {index + 1}, asked by the user")
    await propose_for(task, run, top_insights(lattice), actions_call, config, "user")
    run.updated_at = time.time()
    proposals.save_run(run)
    return run

