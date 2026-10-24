"""The behavior lattice: observations about the user, and the insights built over them layer by layer.

A lattice is built from the events of the types its ``LatticeParams.events``
names (``juno.events``), the way Behavior Latticing (Zhao et al., arXiv
2604.07629, ``latticing``) builds one from any source: an ``activity`` event's
interaction is an action caption with its chunk's dense caption
(``annotate.store``), and these are cut into sessions at a gap of
``session_gap_hours``; a ``message`` event's interaction is one turn of a
conversation (``juno.conversations``), and each conversation is one session.
The observer reads windows of ``window_size`` interactions and writes
observations (layer 0); each layer spec groups the nodes of the layer below
and a model synthesizes each group into insights. Edges go from an insight
down to the nodes it was drawn from.

Node ids count from 0 within their layer, so a node is named by its layer and
id. ``Lattice`` is the stored result; ``LatticeBuild`` is its record, kept
from the moment a build starts so a client can watch it.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from juno.config import LayerSpec
from juno.events import ACTIVITY, MESSAGE, EventType

BuildStatus = Literal["observing", "building", "done", "failed"]


class LatticeParams(BaseModel):
    """Everything about a build that is not the data: the knobs of the method."""

    # The name the prompts call the user.
    user_name: str
    # What the prompts say the interactions are, e.g. "<name>'s screen activities".
    description: str
    # Interactions per observer window.
    window_size: int
    # Insights the model is asked for per group, at least.
    min_insights: int
    # Hours without an activity interaction that start a new session.
    session_gap_hours: float
    # Layer 1 first; the last entry is the top of the lattice.
    layers: list[LayerSpec]
    # The event types read (``juno.events``).
    events: list[EventType] = Field(default_factory=lambda: [ACTIVITY, MESSAGE])
    # Characters of a turn's text the observer is shown, at most.
    turn_chars: int = 2000


class ConversationRef(BaseModel):
    """One turn of a stored conversation (``server.conversations``)."""

    harness: str
    device_id: str
    session_id: str
    # Index into the conversation's ``turns``.
    turn: int


class SourceInteraction(BaseModel):
    """Where one interaction came from in the store: an activity caption's action, or a conversation's turn."""

    # The activity caption (``annotate.store.caption_id``) whose action this is; None for a turn.
    caption_id: Optional[str] = None
    # Index of the action in that caption's ``annotation.actions``; None for a turn.
    action: Optional[int] = None
    # The turn; None for an action.
    conversation: Optional[ConversationRef] = None
    # Unix seconds of the action's first frame, or of the turn.
    time: float


class SourceSession(BaseModel):
    """One session the interactions were cut into; ``metadata.input_session`` of a node indexes these."""

    index: int
    # Unix seconds of the first and last interaction.
    start: float
    end: float
    interactions: list[SourceInteraction]


class NodeMetadata(BaseModel):
    # The session (``LatticeBuild.sessions``) or, above layer 1, the group the node was made from.
    input_session: int
    # ISO datetime of the session's start, as the time layers read it.
    time: Optional[str] = None


class Observation(BaseModel):
    """A layer 0 node: one thing the observer inferred, or the behavior it inferred it from."""

    id: int
    observation: str
    # 1-10, the observer's confidence in the inference.
    confidence: int
    metadata: NodeMetadata


class Insight(BaseModel):
    """A node of layer 1 and above."""

    id: int
    title: str
    tagline: str
    insight: str
    # When the insight applies.
    context: str
    # The model's account of what supports the insight, in prose.
    supporting_evidence: list[str] = []
    # Ids of the nodes in the layer below the insight is drawn from; the
    # layer's edges say the same.
    merged: list[int] = []
    metadata: NodeMetadata

    @field_validator("supporting_evidence", mode="before")
    @classmethod
    def evidence_as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("merged", mode="before")
    @classmethod
    def merged_as_ints(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, list):
            return [int(item) for item in value if str(item).strip().lstrip("-").isdigit()]
        return value


class Edge(BaseModel):
    # An insight id in the layer the edge belongs to.
    source: int
    # A node id in the layer below.
    target: int


class InsightLayer(BaseModel):
    spec: LayerSpec
    insights: list[Insight]
    edges: list[Edge]


class LatticeBuild(BaseModel):
    """The record of one build, written when it starts and updated as it runs."""

    id: str
    # The device whose events were read; None for every device's.
    device_id: Optional[str]
    # Unix seconds; only captions whose frames, and conversations with a turn, in this range were read.
    since: Optional[float]
    until: Optional[float]
    status: BuildStatus
    # Unix seconds.
    created_at: float
    updated_at: float
    finished_at: Optional[float] = None
    # Role -> litellm model string: observer, insight, evidence, format.
    models: dict[str, str]
    params: LatticeParams
    # The sessions the interactions were cut into, in order.
    sessions: list[SourceSession]
    # Activity captions read.
    captions: int
    # Conversations read, and their turns.
    conversations: int = 0
    turns: int = 0
    # Nodes per layer, layer 0 first, as far as the build has got.
    node_counts: list[int] = []
    # Stage name (observe, layer_1, layer_2, ...) -> seconds it took.
    stage_seconds: dict[str, float] = {}
    # Stage name -> model calls made, and how many of them failed (their
    # window, group or insight is left out of the lattice).
    stage_calls: dict[str, int] = {}
    stage_failures: dict[str, int] = {}
    error: Optional[str] = None


class Lattice(BaseModel):
    build: LatticeBuild
    # Layer 0.
    observations: list[Observation]
    # Layer 1 upward.
    layers: list[InsightLayer]


Node = Union[Observation, Insight]


def layer_nodes(lattice: Lattice, layer: int) -> list[Node]:
    """The nodes of a layer, layer 0 being the observations; empty if there is no such layer."""
    if layer == 0:
        return list(lattice.observations)
    if 1 <= layer <= len(lattice.layers):
        return list(lattice.layers[layer - 1].insights)
    return []
