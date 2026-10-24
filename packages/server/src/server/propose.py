"""Starts proposal runs: on request (``POST /proposals/runs``) and on the server's schedule.

``start_run`` records a run and runs Dawn over its day as an ``asyncio`` task
kept in ``app.state.proposal_tasks``, so one run goes at a time. The
``ProposalScheduler`` waits for ``proposals.propose_at`` each day and
proposes for the day before, over the newest finished lattice, unless that
day already has a run.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional

from starlette.datastructures import State

from juno.config import DawnConfig, ProposalsConfig
from lattice.proposals import ProposalRun, new_run, propose_run

logger = logging.getLogger(__name__)


class NoLattice(Exception):
    """No finished lattice build to steer a run by."""


class RunInProgress(Exception):
    """A run is going; its id is the message."""


def running_runs(tasks: dict[str, "asyncio.Task[None]"]) -> list[str]:
    """The ids of the runs still going; finished tasks are dropped from ``tasks``."""
    for run_id in [run_id for run_id, task in tasks.items() if task.done()]:
        del tasks[run_id]
    return list(tasks)


def newest_done_build(state: State) -> Optional[str]:
    return next((b.id for b in state.lattices.builds() if b.status == "done"), None)


def start_run(state: State, day: str, device_id: Optional[str], build_id: Optional[str],
              config: Optional[DawnConfig] = None) -> ProposalRun:
    """Record a run over ``day`` and set it going; ``config`` overrides the server's ``dawn`` knobs."""
    running = running_runs(state.proposal_tasks)
    if running:
        raise RunInProgress(running[0])
    build = build_id if build_id is not None else newest_done_build(state)
    if build is None:
        raise NoLattice("no finished lattice build; build one first")
    dawn = config if config is not None else state.dawn
    run = new_run(dawn, state.lattice.user_name, day, build, device_id)
    state.proposals.save_run(run)
    state.proposal_tasks[run.id] = asyncio.create_task(propose_run(
        run, state.captions, state.lattices, state.proposals, dawn, state.lattice, state.dawn_call))
    return run


def seconds_until(now: datetime.datetime, hhmm: str) -> float:
    """Seconds from ``now`` to the next ``HH:MM`` on the same clock, a day ahead if it has passed."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= now:
        at += datetime.timedelta(days=1)
    return (at - now).total_seconds()


def day_before(now: datetime.datetime) -> str:
    return (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def has_run(state: State, day: str) -> bool:
    return any(r.day == day and r.status != "failed" for r in state.proposals.runs())


class ProposalScheduler:
    """Proposes for the day before at ``propose_at`` every day, while the server runs."""

    def __init__(self, state: State, config: ProposalsConfig):
        self.state = state
        self.config = config
        self.task: Optional["asyncio.Task[None]"] = None

    def start(self) -> None:
        if self.config.propose_at is not None and self.task is None:
            self.task = asyncio.create_task(self.loop())

    def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.task = None

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(seconds_until(datetime.datetime.now(), self.config.propose_at))
            day = day_before(datetime.datetime.now())
            if has_run(self.state, day):
                continue
            try:
                run = start_run(self.state, day, None, None)
                logger.info("proposing for %s as %s", day, run.id)
            except (NoLattice, RunInProgress) as exc:
                logger.warning("not proposing for %s: %s", day, exc)
