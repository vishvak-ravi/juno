"""Propose agent actions for one day of activity, steered by a built lattice's insights.

    uv run python scripts/propose_actions.py --day YYYY-MM-DD [--build BUILD_ID]
        [--device ID] [--threshold T] [--insights-per-task N] [--actions-per-window N]
        [--rerank voyage/<model>|llm] [--dry-run]

Dawn's pipeline (``lattice.proposals.propose_run``, the same the server runs
for ``POST /proposals/runs``) over the top layer of the lattice named by
``--build`` (by default the newest finished build under ``<data dir>/lattice``):
the day's action captions are read from the server's store, the day's
high-level tasks induced from them, each task scored for how much the
insights would change the help with it, every insight ranked against each
task, and two actions proposed for the tasks at or over the threshold.
Models and knobs come from ``dawn`` in ``config.yaml``; the model calls go
where ``lattice.api_base`` points.

The store is only read. The run is written under
``<data dir>/proposals/<run id>/`` (``run.json`` with every task, ``calls.jsonl``
with one line per model call), where the server's ``/proposals`` routes list it.
``--dry-run`` prints the day's actions and the insights and stops before any call.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from dotenv import find_dotenv, load_dotenv

from annotate.store import FileActivityCaptions
from juno.config import ConfigError, devices_dir, load_config, resolve_data_dir
from lattice.dawn import day_actions
from lattice.proposals import (FileProposals, ProposalRun, day_bounds, new_run, propose_run, proposals_dir,
                               top_insights)
from lattice.store import FileLattices, lattice_dir

logger = logging.getLogger("propose_actions")


def newest_done_build(lattices: FileLattices) -> Optional[str]:
    return next((b.id for b in lattices.builds() if b.status == "done"), None)


def print_run(run: ProposalRun) -> None:
    for task in run.tasks:
        print(f"{'' if task.score is None else task.score:>5} {'asked' if task.asked else '     '} {task.task}")
    for task in run.tasks:
        if not task.asked:
            continue
        print(f"\n## {task.task} (insights {[r.id for r in task.ranked if r.given]})")
        for action in task.actions:
            print(f"- {action.title}: {action.action}")
        for cut in task.considered:
            print(f"  cut: {cut.title}: {cut.why}")
        if task.error:
            print(f"  failed: {task.error}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--day", required=True, help="local calendar day, YYYY-MM-DD")
    parser.add_argument("--build", help="lattice build id; default: the newest finished build")
    parser.add_argument("--device", help="only this device's captions; default: every device's")
    parser.add_argument("--threshold", type=float, help="default: dawn.utility_threshold")
    parser.add_argument("--insights-per-task", type=int, help="default: dawn.insights_per_task")
    parser.add_argument("--actions-per-window", type=int, help="default: dawn.actions_per_window")
    parser.add_argument("--rerank", help="voyage/<model> or llm; default: dawn.rerank_model")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    dawn = config.dawn.model_copy(update={k: v for k, v in {
        "utility_threshold": args.threshold, "insights_per_task": args.insights_per_task,
        "actions_per_window": args.actions_per_window, "rerank_model": args.rerank}.items()
        if v is not None})
    data_dir = resolve_data_dir(config.capture)
    lattices = FileLattices(lattice_dir(data_dir))
    captions = FileActivityCaptions(devices_dir(data_dir))
    build_id = args.build or newest_done_build(lattices)
    if build_id is None:
        print("no finished lattice build; run scripts/build_lattice.py first", file=sys.stderr)
        return 1
    if args.dry_run:
        lattice = lattices.get(build_id)
        if lattice is None:
            print(f"build {build_id} has no lattice", file=sys.stderr)
            return 1
        since, until = day_bounds(args.day)
        print("\n".join(day_actions(captions.all(device_id=args.device, since=since, until=until))))
        print()
        for insight in top_insights(lattice):
            print(f"[{insight.id}] {insight.title}: {insight.tagline}")
        return 0
    run = new_run(dawn, config.lattice.user_name, args.day, build_id, args.device)
    proposals = FileProposals(proposals_dir(data_dir))
    run = asyncio.run(propose_run(run, captions, lattices, proposals, dawn, config.lattice))
    print_run(run)
    if run.status == "failed":
        print(f"failed: {run.error}", file=sys.stderr)
        return 1
    logger.info("wrote %s in %.1f s", proposals.run_dir(run.id), run.seconds or 0.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
