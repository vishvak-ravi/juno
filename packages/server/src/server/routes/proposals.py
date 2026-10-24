"""``/proposals``: Dawn's proposed agent actions per day, and what the user decided about each.

``GET /proposals`` lists every proposal of every run, newest run first, each
with the state its trail puts it in; ``?state=`` keeps one list (``open``,
``accepted``, ``almost``, ``dismissed``). A proposal a page returns for the
first time gets a ``listed`` event, so shown can be counted. ``GET
/proposals/{id}`` is one proposal in full: its task with every insight ranked,
the reframings, the candidates cut, the run's model calls, the day's sessions
and the trail. ``PATCH /proposals/{id}`` appends one event to the trail.

``GET /proposals/runs`` lists the runs; ``POST /proposals/runs`` starts one
over a day (202; 409 while one goes); ``GET /proposals/runs/{run}`` is a run
with every task and the state of each proposal; ``POST
/proposals/runs/{run}/tasks/{n}`` runs the proposer for a task under the
line. ``GET /proposals/rate`` counts shown, decided, accepted and almost over
a range of listing times.
"""

import time
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from starlette.datastructures import State

from juno.sessions import DEVICE_ID_PATTERN
from lattice.models import Insight
from lattice.proposals import (DAY_PATTERN, PROPOSAL_ID_PATTERN, RUN_ID_PATTERN, Bucket, Decision, ModelCallRecord,
                               ProposalRun, ProposalState, ProposalTask, RunSession, RunStatus, bucket_of,
                               parse_proposal_id, proposal_id, propose_task, state_of, top_insights)
from server.propose import NoLattice, RunInProgress, start_run
from server.routes.paging import DeviceFilter, Page, page_of

router = APIRouter(prefix="/proposals", tags=["proposals"])

RunId = Annotated[str, PathParam(pattern=f"^{RUN_ID_PATTERN}$", max_length=40)]
ProposalId = Annotated[str, PathParam(pattern=f"^{PROPOSAL_ID_PATTERN}$", max_length=48)]
SERVER = "server"


class TaskRef(BaseModel):
    """The task a listed proposal came from."""

    index: int
    task: str
    score: Optional[float]
    asked_by: Optional[Literal["threshold", "user"]]


class ProposalSummary(BaseModel):
    """One proposal as the lists return it."""

    id: str
    run_id: str
    day: str
    device_id: Optional[str]
    build_id: str
    # When the run was made.
    created_at: float
    title: str
    action: str
    benefit: str
    # The given insights the action cites, by id in the build's top layer.
    insight_ids: list[int]
    task: TaskRef
    state: ProposalState
    bucket: Bucket


class RunSummary(BaseModel):
    """A run without its tasks, with what it came to."""

    id: str
    day: str
    device_id: Optional[str]
    build_id: str
    status: RunStatus
    created_at: float
    updated_at: float
    finished_at: Optional[float]
    seconds: Optional[float]
    user_name: str
    models: dict[str, str]
    utility_threshold: float
    insights_per_task: int
    actions_per_window: int
    # Tasks this close under the threshold are almost asked (``proposals.near_threshold``).
    near_threshold: float
    actions: int
    insights: int
    sessions: list[RunSession]
    error: Optional[str]
    tasks: int
    asked: int
    proposals: int
    accepted: int
    open: int


class Proposal(ProposalSummary):
    """One proposal in full."""

    run: RunSummary
    whole_task: ProposalTask
    # The build's top layer, the insights ``whole_task.ranked`` names.
    insights: list[Insight]
    # The run's calls; the task's own with what was sent and came back, the rest without.
    calls: list[ModelCallRecord]
    trail: list[Decision]


class RunDetail(ProposalRun):
    """A run with every task, and the state of each of its proposals by id."""

    near_threshold: float
    states: dict[str, ProposalState] = {}


class DecisionRequest(BaseModel):
    """One event of a trail, from a client."""

    event: Literal["opened", "expanded", "edited", "accepted", "dismissed", "snoozed", "reopened"]
    where: str = Field(pattern=r"^[a-z]+$", max_length=20)
    detail: Optional[str] = Field(default=None, max_length=4000)
    dwell: Optional[float] = Field(default=None, ge=0)


class RunRequest(BaseModel):
    """A day to propose over; every knob left out is taken from ``dawn`` in ``config.yaml``."""

    day: str = Field(pattern=f"^{DAY_PATTERN}$")
    device: Optional[str] = Field(default=None, pattern=f"^{DEVICE_ID_PATTERN}$")
    # The newest finished build when None.
    build_id: Optional[str] = Field(default=None, pattern="^lattice_\\d{8}_\\d{6}$")
    utility_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    insights_per_task: Optional[int] = Field(default=None, ge=1)
    actions_per_window: Optional[int] = Field(default=None, ge=0)


class Rate(BaseModel):
    """Proposals listed in a range of time, by what became of them."""

    shown: int
    decided: int
    accepted: int
    almost: int
    open: int


def listed_at(trail: list[Decision]) -> Optional[float]:
    return next((d.at for d in trail if d.event == "listed"), None)


def states_of(state: State, run: ProposalRun) -> dict[str, ProposalState]:
    trails = state.proposals.trails(run.id)
    return {proposal_id(run.id, task.index, n): state_of(trails.get(proposal_id(run.id, task.index, n), []),
                                                          state.proposals_config.almost_seconds)
            for task in run.tasks for n in range(len(task.actions))}


def summary_of(run: ProposalRun, task: ProposalTask, n: int, state: ProposalState) -> ProposalSummary:
    action = task.actions[n]
    return ProposalSummary(id=proposal_id(run.id, task.index, n), run_id=run.id, day=run.day, device_id=run.device_id,
                           build_id=run.build_id, created_at=run.created_at, title=action.title, action=action.action,
                           benefit=action.benefit, insight_ids=action.insight_ids,
                           task=TaskRef(index=task.index, task=task.task, score=task.score, asked_by=task.asked_by),
                           state=state, bucket=bucket_of(state))


def run_summary(state: State, run: ProposalRun) -> RunSummary:
    states = states_of(state, run)
    return RunSummary(**run.model_dump(exclude={"tasks"}), near_threshold=state.proposals_config.near_threshold,
                      tasks=len(run.tasks), asked=sum(1 for t in run.tasks if t.asked), proposals=len(states),
                      accepted=sum(1 for s in states.values() if s.state == "accepted"),
                      open=sum(1 for s in states.values() if s.decided is None))


def summaries(state: State, device: Optional[str]) -> list[ProposalSummary]:
    """Every proposal of every run, oldest run first, in task and action order."""
    rows = []
    for run in reversed(state.proposals.runs()):
        if device is not None and run.device_id not in (None, device):
            continue
        states = states_of(state, run)
        for task in run.tasks:
            for n in range(len(task.actions)):
                rows.append(summary_of(run, task, n, states[proposal_id(run.id, task.index, n)]))
    return rows


def find_run(state: State, run_id: str) -> ProposalRun:
    run = state.proposals.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no proposal run with id {run_id}")
    return run


def find_proposal(state: State, graded_id: str) -> tuple[ProposalRun, ProposalTask, int]:
    located = parse_proposal_id(graded_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"no proposal with id {graded_id}")
    run_id, task_index, action = located
    run = find_run(state, run_id)
    if not (0 <= task_index < len(run.tasks) and 0 <= action < len(run.tasks[task_index].actions)):
        raise HTTPException(status_code=404, detail=f"no proposal with id {graded_id}")
    return run, run.tasks[task_index], action


def task_calls(calls: list[ModelCallRecord], task: ProposalTask) -> list[ModelCallRecord]:
    """The run's calls, those for ``task`` with their prompt and reply, the others without."""
    mine = f"task {task.index + 1}"
    return [c if c.what == mine or c.what.startswith(f"{mine},") else c.model_copy(update={"prompt": None, "reply": None})
            for c in calls]


def proposal_of(state: State, graded_id: str) -> Proposal:
    run, task, n = find_proposal(state, graded_id)
    trail = state.proposals.trails(run.id).get(graded_id, [])
    summary = summary_of(run, task, n, state_of(trail, state.proposals_config.almost_seconds))
    lattice = state.lattices.get(run.build_id)
    return Proposal(**summary.model_dump(), run=run_summary(state, run), whole_task=task,
                    insights=top_insights(lattice) if lattice is not None else [],
                    calls=task_calls(state.proposals.calls(run.id), task), trail=trail)


@router.get("/runs")
def list_runs(request: Request) -> list[RunSummary]:
    """Every run, newest first, with its counts."""
    state = request.app.state
    return [run_summary(state, run) for run in state.proposals.runs()]


@router.post("/runs", status_code=202)
async def create_run(body: RunRequest, request: Request) -> RunSummary:
    """Propose over a day; 202 with the run while it goes, 409 while another does."""
    state = request.app.state
    overrides = {k: v for k, v in {"utility_threshold": body.utility_threshold, "insights_per_task": body.insights_per_task,
                                   "actions_per_window": body.actions_per_window}.items() if v is not None}
    try:
        run = start_run(state, body.day, body.device, body.build_id, state.dawn.model_copy(update=overrides))
    except RunInProgress as exc:
        raise HTTPException(status_code=409, detail={"message": f"run {exc} is going; wait for it to finish",
                                                     "run_id": str(exc)})
    except NoLattice as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return run_summary(state, run)


@router.get("/runs/{run_id}")
def get_run(run_id: RunId, request: Request) -> RunDetail:
    """The run with every task and each proposal's state."""
    state = request.app.state
    run = find_run(state, run_id)
    return RunDetail(**run.model_dump(), near_threshold=state.proposals_config.near_threshold,
                     states=states_of(state, run))


@router.post("/runs/{run_id}/tasks/{index}")
async def propose_for_task(run_id: RunId, index: Annotated[int, PathParam(ge=0)], request: Request) -> RunDetail:
    """Run the proposer for one task the threshold left out; 409 unless the run is done."""
    state = request.app.state
    run = find_run(state, run_id)
    if not 0 <= index < len(run.tasks):
        raise HTTPException(status_code=404, detail=f"run {run_id} has no task {index}")
    if run.status != "done":
        raise HTTPException(status_code=409, detail=f"run {run_id} is {run.status}")
    try:
        run = await propose_task(run, index, state.lattices, state.proposals, state.dawn, state.lattice, state.dawn_call)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RunDetail(**run.model_dump(), near_threshold=state.proposals_config.near_threshold,
                     states=states_of(state, run))


@router.get("/rate")
def get_rate(request: Request, since: Optional[float] = None, until: Optional[float] = None) -> Rate:
    """What became of the proposals listed between ``since`` and ``until``."""
    state = request.app.state
    counts = Rate(shown=0, decided=0, accepted=0, almost=0, open=0)
    for run in state.proposals.runs():
        trails = state.proposals.trails(run.id)
        for item_id, trail in trails.items():
            at = listed_at(trail)
            if at is None or (since is not None and at < since) or (until is not None and at >= until):
                continue
            item = state_of(trail, state.proposals_config.almost_seconds)
            counts.shown += 1
            counts.decided += item.decided is not None
            counts.accepted += item.state == "accepted"
            counts.almost += item.almost
            counts.open += item.decided is None
    return counts


@router.get("")
def list_proposals(
    request: Request,
    state: Optional[Bucket] = None,
    device: DeviceFilter = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[ProposalSummary]:
    """Every proposal, newest run first by default; ``state`` keeps one list."""
    app_state = request.app.state
    rows = [row for row in summaries(app_state, device) if state is None or row.bucket == state]
    page = page_of(rows, limit, order, after)
    now = time.time()
    for row in page.data:
        if listed_at(app_state.proposals.trails(row.run_id).get(row.id, [])) is None:
            app_state.proposals.add_decision(row.run_id, row.id, Decision(event="listed", at=now, where=SERVER))
    return page


@router.get("/{graded_id}")
def get_proposal(graded_id: ProposalId, request: Request) -> Proposal:
    """One proposal in full."""
    return proposal_of(request.app.state, graded_id)


@router.patch("/{graded_id}")
def decide(graded_id: ProposalId, body: DecisionRequest, request: Request) -> Proposal:
    """Append one event to the proposal's trail and answer with the proposal as it now stands."""
    state = request.app.state
    run, _, _ = find_proposal(state, graded_id)
    state.proposals.add_decision(run.id, graded_id, Decision(event=body.event, at=time.time(), where=body.where,
                                                             detail=body.detail, dwell=body.dwell))
    return proposal_of(state, graded_id)
