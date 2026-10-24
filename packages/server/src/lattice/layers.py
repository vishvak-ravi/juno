"""How a ``LayerSpec`` groups the nodes of the layer below, as the ``latticing`` library's layers do.

``session``: the nodes are sorted by ``metadata.input_session`` and every
``value`` consecutive sessions make one group. ``time``: sorted by the calendar
unit ``value`` (day, week, month, year) of ``metadata.time``, one group per
unit that has nodes. ``number``: ``value`` nodes per group, in the order given.
``all``: one group of every node. Sorts are stable, so nodes of one session or
unit keep their id order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence, TypeVar

from juno.config import LayerSpec
from lattice.models import Node

NodeT = TypeVar("NodeT", bound=Node)

TIME_UNITS = ("day", "week", "month", "year")


def time_key(node: Node, by: str) -> tuple:
    """The calendar unit ``by`` that ``node``'s time falls in."""
    if node.metadata.time is None:
        raise ValueError(f"node {node.id} has no time in its metadata")
    at = datetime.fromisoformat(node.metadata.time)
    if by == "day":
        return (at.year, at.month, at.day)
    if by == "week":
        iso = at.isocalendar()
        return (iso.year, iso.week)
    if by == "month":
        return (at.year, at.month)
    return (at.year,)


def split_by_session(nodes: Sequence[NodeT], n: int) -> list[list[NodeT]]:
    buckets: dict[int, list[NodeT]] = {}
    for node in sorted(nodes, key=lambda node: node.metadata.input_session):
        buckets.setdefault(node.metadata.input_session, []).append(node)
    ordered = list(buckets.values())
    return [[node for session in ordered[i:i + n] for node in session]
            for i in range(0, len(ordered), n)]


def split_by_time(nodes: Sequence[NodeT], by: str) -> list[list[NodeT]]:
    if by not in TIME_UNITS:
        raise ValueError(f"time layer unit must be one of {TIME_UNITS}; got {by!r}")
    groups: list[list[NodeT]] = []
    current = None
    for node in sorted(nodes, key=lambda node: time_key(node, by)):
        key = time_key(node, by)
        if key != current:
            groups.append([])
            current = key
        groups[-1].append(node)
    return groups


def split_by_number(nodes: Sequence[NodeT], n: int) -> list[list[NodeT]]:
    return [list(nodes[i:i + n]) for i in range(0, len(nodes), n)]


def split(spec: LayerSpec, nodes: Sequence[NodeT]) -> list[list[NodeT]]:
    """The groups ``spec`` cuts ``nodes`` into, each synthesized into insights on its own."""
    if spec.type == "session":
        n = int(spec.value) if spec.value is not None else 1
        if n < 1:
            raise ValueError(f"session layer value must be >= 1; got {n}")
        return split_by_session(nodes, n)
    if spec.type == "time":
        return split_by_time(nodes, spec.value or "day")
    if spec.type == "number":
        if spec.value is None:
            raise ValueError("number layer needs a value")
        n = int(spec.value)
        if n < 1:
            raise ValueError(f"number layer value must be >= 1; got {n}")
        return split_by_number(nodes, n)
    return [list(nodes)]
