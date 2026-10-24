"""``/lattice``: the behavior lattices built over a device's events (``lattice``).

``GET /lattice`` lists every build newest first, each without its sessions;
``POST /lattice`` starts one, with the method's knobs taken from the body or,
where the body leaves one out, from ``lattice`` in ``config.yaml``, and answers
202 with the build's record while it runs in the server. One build runs at a
time: a second POST while one runs is refused with 409. A build's record is
``GET /lattice/{build_id}``, its status moving ``observing`` to ``building`` to
``done`` (or ``failed``, with ``error``).

Once done, ``GET /lattice/{build_id}/lattice`` is the whole lattice;
``GET /lattice/{build_id}/layers/{layer}`` one layer (0: the observations and
no edges; above: the insights and their edges down to the layer below), and
``GET /lattice/{build_id}/nodes/{layer}/{node_id}`` one node with the insights
it supports, the nodes it rests on, every node under it down to the
observations, and the sessions and events those observations were drawn from.
"""

import asyncio
import logging
import time
from typing import Annotated, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import State

from annotate.request import framed_rows
from annotate.store import ActivityCaptions, parse_caption_id
from juno.config import LatticeConfig, LayerSpec
from juno.events import EventType, event_id_of
from juno.sessions import DEVICE_ID_PATTERN
from lattice.build import new_build
from lattice.models import (BuildStatus, Edge, Insight, Lattice, LatticeBuild, LatticeParams, Node,
                            Observation, SourceInteraction, SourceSession, layer_nodes)
from lattice.store import BUILD_ID_PATTERN, Lattices
from server.conversations import Conversations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lattice", tags=["lattice"])

BuildId = Annotated[str, PathParam(pattern=f"^{BUILD_ID_PATTERN}$", max_length=64)]

# Runs one build to the end, saving through the ``Lattices`` as it goes: ``lattice.build.build_lattice``.
BuildLattice = Callable[[LatticeBuild, ActivityCaptions, Conversations, Lattices, LatticeConfig],
                        Awaitable[Lattice]]


class BuildSummary(BaseModel):
    """``LatticeBuild`` with the count of its sessions in place of the sessions."""

    id: str
    device_id: Optional[str]
    since: Optional[float]
    until: Optional[float]
    status: BuildStatus
    created_at: float
    updated_at: float
    finished_at: Optional[float] = None
    models: dict[str, str]
    params: LatticeParams
    sessions: int
    captions: int
    conversations: int = 0
    turns: int = 0
    node_counts: list[int] = []
    stage_seconds: dict[str, float] = {}
    stage_calls: dict[str, int] = {}
    stage_failures: dict[str, int] = {}
    error: Optional[str] = None


class BuildRequest(BaseModel):
    """What to build over; every field left out is taken from ``lattice`` in ``config.yaml``."""

    # Only this device's events; every device's when None.
    device: Optional[str] = Field(default=None, pattern=f"^{DEVICE_ID_PATTERN}$")
    # Unix seconds; only captions whose frames, and conversations with a turn, in the range are read.
    since: Optional[float] = None
    until: Optional[float] = None
    user_name: Optional[str] = None
    # ``{user_name}`` in it is filled with the name.
    description: Optional[str] = None
    window_size: Optional[int] = Field(default=None, ge=1)
    min_insights: Optional[int] = Field(default=None, ge=1)
    session_gap_hours: Optional[float] = Field(default=None, gt=0)
    layers: Optional[list[LayerSpec]] = Field(default=None, min_length=1)
    events: Optional[list[EventType]] = Field(default=None, min_length=1)
    turn_chars: Optional[int] = Field(default=None, ge=1)


class Layer(BaseModel):
    layer: int
    # None for layer 0, the observations.
    spec: Optional[LayerSpec]
    nodes: list[Node]
    # From this layer's insights down to the nodes of the layer below; empty for layer 0.
    edges: list[Edge]


class InteractionSource(SourceInteraction):
    # The action's caption, or the turn's text; None when neither can be read.
    caption: Optional[str] = None
    # The events the interaction's action covers, oldest first; empty for a turn,
    # and when the session's rows or the caption cannot be read.
    event_ids: list[str] = []


class SessionSource(BaseModel):
    """One of ``LatticeBuild.sessions`` that the node's observations were drawn from."""

    index: int
    # Unix seconds of the first and last interaction.
    start: float
    end: float
    interactions: list[InteractionSource]


class NodeDetail(BaseModel):
    layer: int
    node: Node
    # Insights of the layer above with an edge down to this node.
    parents: list[Insight]
    # Nodes of the layer below this node has an edge down to.
    children: list[Node]
    # Every node under this one, by layer, down to the observations; empty for an observation.
    support: dict[int, list[Node]]
    # The sessions the observations under the node (or the node itself, at
    # layer 0) were drawn from, by index.
    sources: list[SessionSource]


def summary_of(build: LatticeBuild) -> BuildSummary:
    return BuildSummary(**build.model_dump(exclude={"sessions"}), sessions=len(build.sessions))


def build_params(config: LatticeConfig, body: BuildRequest) -> LatticeParams:
    """The body's knobs over the configured ones, the description naming the user."""
    user_name = body.user_name if body.user_name is not None else config.user_name
    description = body.description if body.description is not None else config.description
    return LatticeParams(
        user_name=user_name,
        description=description.replace("{user_name}", user_name),
        window_size=body.window_size if body.window_size is not None else config.window_size,
        min_insights=body.min_insights if body.min_insights is not None else config.min_insights,
        session_gap_hours=(body.session_gap_hours if body.session_gap_hours is not None
                           else config.session_gap_hours),
        layers=body.layers if body.layers is not None else list(config.layers),
        events=body.events if body.events is not None else list(config.events),
        turn_chars=body.turn_chars if body.turn_chars is not None else config.turn_chars)


def running_builds(tasks: dict[str, "asyncio.Task[None]"]) -> list[str]:
    """The ids of the builds still running; finished tasks are dropped from ``tasks``."""
    for build_id in [build_id for build_id, task in tasks.items() if task.done()]:
        del tasks[build_id]
    return list(tasks)


async def run_and_record(build: LatticeBuild, state: State) -> None:
    """Run the build; a failure it did not record on its own is written to its record."""
    try:
        await state.build_lattice(build, state.captions, state.conversations, state.lattices,
                                  state.lattice)
    except Exception as exc:
        logger.exception("lattice build %s failed", build.id)
        stored = state.lattices.get_build(build.id) or build
        if stored.status not in ("done", "failed"):
            stored.status = "failed"
            stored.error = f"{type(exc).__name__}: {exc}"
            stored.finished_at = stored.updated_at = time.time()
            state.lattices.save_build(stored)


def find_build(state: State, build_id: str) -> LatticeBuild:
    build = state.lattices.get_build(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail=f"no lattice build with id {build_id}")
    return build


def find_lattice(state: State, build_id: str) -> Lattice:
    lattice = state.lattices.get(build_id)
    if lattice is None:
        build = find_build(state, build_id)
        raise HTTPException(status_code=404,
                            detail=f"build {build_id} has no lattice: its status is {build.status}")
    return lattice


def layer_edges(lattice: Lattice, layer: int) -> list[Edge]:
    """The edges from layer ``layer``'s insights to the layer below; empty for layer 0."""
    return list(lattice.layers[layer - 1].edges) if 1 <= layer <= len(lattice.layers) else []


def layer_of(lattice: Lattice, layer: int) -> Layer:
    if not 0 <= layer <= len(lattice.layers):
        raise HTTPException(status_code=404,
                            detail=f"build {lattice.build.id} has layers 0 to {len(lattice.layers)}")
    return Layer(layer=layer, spec=lattice.layers[layer - 1].spec if layer else None,
                 nodes=layer_nodes(lattice, layer), edges=layer_edges(lattice, layer))


def nodes_by_id(lattice: Lattice, layer: int) -> dict[int, Node]:
    return {node.id: node for node in layer_nodes(lattice, layer)}


def support_of(lattice: Lattice, layer: int, node_id: int) -> dict[int, list[Node]]:
    """Every node under the node, by layer, following the edges down to layer 0."""
    support: dict[int, list[Node]] = {}
    ids = {node_id}
    for below in range(layer - 1, -1, -1):
        edges = layer_edges(lattice, below + 1)
        ids = {edge.target for edge in edges if edge.source in ids}
        nodes = nodes_by_id(lattice, below)
        support[below] = [nodes[i] for i in sorted(ids) if i in nodes]
    return support


def interaction_source(state: State, interaction: SourceInteraction) -> InteractionSource:
    """The interaction with its text and, for an action, the ids of the events its frames are rows of."""
    event_ids: list[str] = []
    text: Optional[str] = None
    ref = interaction.conversation
    if ref is not None:
        conversation = state.conversations.get(ref.device_id, ref.harness, ref.session_id)
        if conversation is not None and 0 <= ref.turn < len(conversation.turns):
            text = conversation.turns[ref.turn].text
        return InteractionSource(**interaction.model_dump(), caption=text)
    located = parse_caption_id(interaction.caption_id) if interaction.caption_id else None
    if located is not None:
        session_id, chunk = located
        try:
            caption = state.captions.get(session_id, chunk)
        except ValidationError as exc:
            logger.warning("skipping the activity caption of %s chunk %d: %s", session_id, chunk, exc)
            caption = None
        actions = caption.annotation.actions if caption is not None and caption.annotation else []
        if interaction.action is not None and 0 <= interaction.action < len(actions):
            action = actions[interaction.action]
            text = action.caption
            framed = framed_rows(state.events.rows(session_id), chunk, state.annotate.chunk_size)
            event_ids = [event_id_of(session_id, row) for row in framed[action.start - 1:action.end]]
    return InteractionSource(**interaction.model_dump(), caption=text, event_ids=event_ids)


def session_source(state: State, session: SourceSession) -> SessionSource:
    return SessionSource(index=session.index, start=session.start, end=session.end,
                         interactions=[interaction_source(state, i) for i in session.interactions])


def sources_of(state: State, lattice: Lattice, observations: list[Observation]) -> list[SessionSource]:
    """The sessions the observations were drawn from, by index."""
    sessions = {session.index: session for session in lattice.build.sessions}
    indices = sorted({o.metadata.input_session for o in observations} & set(sessions))
    return [session_source(state, sessions[index]) for index in indices]


def node_detail(state: State, lattice: Lattice, layer: int, node_id: int) -> NodeDetail:
    node = nodes_by_id(lattice, layer).get(node_id)
    if node is None:
        raise HTTPException(status_code=404,
                            detail=f"build {lattice.build.id} has no node {node_id} in layer {layer}")
    above = nodes_by_id(lattice, layer + 1)
    parents = [above[edge.source] for edge in layer_edges(lattice, layer + 1)
               if edge.target == node_id and edge.source in above]
    below = nodes_by_id(lattice, layer - 1) if layer else {}
    children = [below[edge.target] for edge in layer_edges(lattice, layer)
                if edge.source == node_id and edge.target in below]
    support = support_of(lattice, layer, node_id)
    observations = [n for n in support.get(0, []) if isinstance(n, Observation)]
    if layer == 0 and isinstance(node, Observation):
        observations = [node]
    return NodeDetail(layer=layer, node=node, parents=parents, children=children, support=support,
                      sources=sources_of(state, lattice, observations))


@router.get("")
def list_builds(request: Request) -> list[BuildSummary]:
    """Every build, newest first, without its sessions."""
    return [summary_of(build) for build in request.app.state.lattices.builds()]


@router.post("", status_code=202)
async def start_build(body: BuildRequest, request: Request) -> BuildSummary:
    """Start a build over the events in the range and answer with its record; 409 while one runs."""
    state = request.app.state
    running = running_builds(state.lattice_tasks)
    if running:
        raise HTTPException(status_code=409, detail={
            "message": f"build {running[0]} is running; wait for it to finish",
            "build_id": running[0]})
    build = new_build(state.lattice, build_params(state.lattice, body), body.device, body.since,
                      body.until)
    state.lattices.save_build(build)
    # Kept on the app so the task is not collected before it ends.
    state.lattice_tasks[build.id] = asyncio.create_task(run_and_record(build, state))
    return summary_of(build)


@router.get("/{build_id}")
def get_build(build_id: BuildId, request: Request) -> LatticeBuild:
    """The build's record with its sessions."""
    return find_build(request.app.state, build_id)


@router.get("/{build_id}/lattice")
def get_lattice(build_id: BuildId, request: Request) -> Lattice:
    """The whole lattice; 404 until the build is done."""
    return find_lattice(request.app.state, build_id)


@router.get("/{build_id}/layers/{layer}")
def get_layer(build_id: BuildId, layer: Annotated[int, PathParam(ge=0)], request: Request) -> Layer:
    """One layer's nodes and its edges down to the layer below."""
    return layer_of(find_lattice(request.app.state, build_id), layer)


@router.get("/{build_id}/nodes/{layer}/{node_id}")
def get_node(build_id: BuildId, layer: Annotated[int, PathParam(ge=0)],
             node_id: Annotated[int, PathParam(ge=0)], request: Request) -> NodeDetail:
    """One node with what it supports, what it rests on, and where it came from."""
    state = request.app.state
    return node_detail(state, find_lattice(state, build_id), layer, node_id)
