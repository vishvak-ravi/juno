"""Builds a lattice from a device's events and stores it as it goes.

The method is Behavior Latticing's (Zhao et al., arXiv 2604.07629), as the
``latticing`` library runs it over Tada's captions in its ``examples/tada.py``
and over chat threads in its ``examples/chat.ipynb``; the observer reads one
shape, so each event type the build names (``LatticeParams.events``) has its
own reader into it:

1. Interactions and sessions. ``activity``: one interaction per action of every
   done caption, its text the action's caption followed by the chunk's dense
   caption, its time the action's first frame's screenshot time, ordered by
   time; a gap of ``session_gap_hours`` or more between two interactions starts
   a new session. ``message``: one interaction per turn of every stored
   conversation, its text the speaker's name and the turn's first
   ``turn_chars`` characters; each conversation is one session. The sessions of
   every type are ordered by their first interaction.
3. Observe (layer 0): each session is read in windows of ``window_size``
   interactions with ``OBSERVE_PROMPT``; each observation the observer writes
   becomes two nodes, what the user thinks or feels and the actions it was
   inferred from.
4. Layer 1: the first ``LayerSpec`` groups the observations; each group is
   turned into insight prose (``OBSERVATION_TO_INSIGHT_PROMPT``), the prose into
   JSON (``FORMAT_INSIGHT_PROMPT``), and each insight is mapped to the
   observations that support it (``MAP_EVIDENCE_PROMPT``), which are its edges.
5. Layers 2 and up: each further spec groups the insights below; each group is
   synthesized into insights (``INSIGHT_SYNTHESIS_PROMPT``) whose ``merged``
   ids are its edges.

``max_concurrency`` calls are in flight at once. A call that fails, or whose
reply has no JSON even after the format model is asked to fix it, costs only
its own window, group or insight; it is logged and counted in the build's
``stage_failures``. The build record is saved as each stage ends.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Optional, Sequence, TypeVar, Union

from pydantic import BaseModel, ValidationError

from annotate.store import ActivityCaption, ActivityCaptions
from juno.config import LatticeConfig, LayerSpec
from juno.conversations import Conversation, assistant_name
from juno.events import ACTIVITY, MESSAGE
from lattice.layers import split
from lattice.llm import ModelCall, Reply, litellm_call
from lattice.models import (ConversationRef, Edge, Insight, InsightLayer, Lattice, LatticeBuild,
                            LatticeParams, NodeMetadata, Observation, SourceInteraction, SourceSession)
from server.conversations import Conversations
from lattice.parse import (InsightsReply, ObservationsReply, SupportingIdsReply, fallback_prompt,
                           parse_model_json)
from lattice.prompts import (FORMAT_INSIGHT_PROMPT, INSIGHT_SYNTHESIS_PROMPT, MAP_EVIDENCE_PROMPT,
                             OBSERVATION_TO_INSIGHT_PROMPT, OBSERVE_PROMPT)
from lattice.store import Lattices, new_build_id

logger = logging.getLogger(__name__)

# How the prompts and the time layers read an interaction's time.
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# The metadata key the observer is shown before each interaction's text.
TIME_KEY = "time"
# The observer writes each observation as these two texts; each becomes a node.
OBSERVATION_TEXTS = ("think_feel", "actions")
OBSERVE_STAGE = "observe"

T = TypeVar("T")
Outcome = Union[T, BaseException]


class Interaction(BaseModel):
    """One event as the observer reads it (an action caption, or a turn), and where it came from."""

    text: str
    # ``TIME_FORMAT``, local time.
    time: str
    source: SourceInteraction


class StageStats(BaseModel):
    """What one stage cost: model calls, failures, token totals when the provider reported them."""

    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Replies that came with a usage report.
    counted: int = 0

    def record(self, reply: Reply) -> None:
        if reply.prompt_tokens is not None and reply.completion_tokens is not None:
            self.prompt_tokens += reply.prompt_tokens
            self.completion_tokens += reply.completion_tokens
            self.counted += 1


def time_string(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).strftime(TIME_FORMAT)


def interaction_time(caption: ActivityCaption, start: int) -> Optional[float]:
    """The screenshot time of frame ``start`` (1-based), else the caption's start time."""
    if 1 <= start <= len(caption.frames) and caption.frames[start - 1].timestamp is not None:
        return caption.frames[start - 1].timestamp
    return caption.start_time


def interactions_of(captions: Sequence[ActivityCaption]) -> list[Interaction]:
    """One interaction per action of every done caption, ordered by time."""
    interactions = []
    for caption in captions:
        if caption.status != "done" or caption.annotation is None:
            continue
        for index, action in enumerate(caption.annotation.actions):
            at = interaction_time(caption, action.start)
            if at is None:
                logger.warning("%s action %d has no time; skipped", caption.id, index)
                continue
            interactions.append(Interaction(
                text=f"{action.caption} {caption.annotation.dense_caption}",
                time=time_string(at),
                source=SourceInteraction(caption_id=caption.id, action=index, time=at)))
    return sorted(interactions, key=lambda i: i.source.time)


def sessionize(interactions: Sequence[Interaction], gap_hours: float) -> list[list[Interaction]]:
    """Cut the interactions where the gap to the previous one is ``gap_hours`` or more.

    Gaps are measured on whole seconds, as the time strings the prompts carry.
    """
    if not interactions:
        return []
    sessions = [[interactions[0]]]
    for interaction in interactions[1:]:
        previous = sessions[-1][-1]
        gap = math.floor(interaction.source.time) - math.floor(previous.source.time)
        if gap >= gap_hours * 3600:
            sessions.append([])
        sessions[-1].append(interaction)
    return sessions


def clipped(text: str, chars: int) -> str:
    """The first ``chars`` characters of ``text``, marked as cut when there were more."""
    return text if len(text) <= chars else text[:chars].rstrip() + " [...]"


def conversation_sessions(conversations: Sequence[Conversation], user_name: str,
                          turn_chars: int) -> list[list[Interaction]]:
    """One session per conversation, one interaction per turn: the speaker's name and the turn's text."""
    sessions = []
    for conversation in conversations:
        names = {"user": user_name, "assistant": assistant_name(conversation.harness)}
        order = sorted(range(len(conversation.turns)), key=lambda index: conversation.turns[index].time)
        session = [Interaction(
            text=f"{names[turn.role]}: {clipped(turn.text, turn_chars)}", time=time_string(turn.time),
            source=SourceInteraction(conversation=ConversationRef(
                harness=conversation.harness, device_id=conversation.device_id,
                session_id=conversation.session_id, turn=index), time=turn.time))
            for index in order for turn in [conversation.turns[index]]]
        if session:
            sessions.append(session)
    return sessions


def source_sessions(sessions: Sequence[Sequence[Interaction]]) -> list[SourceSession]:
    return [SourceSession(index=index, start=session[0].source.time, end=session[-1].source.time,
                          interactions=[i.source for i in session])
            for index, session in enumerate(sessions)]


def window_text(window: Sequence[Interaction]) -> str:
    """The interactions as the observer is shown them: each one's metadata, then its text."""
    lines = []
    for interaction in window:
        lines.extend([TIME_KEY, interaction.time, interaction.text])
    return "\n".join(lines)


def fmt_observations(nodes: Sequence[Observation]) -> str:
    return "\n".join(f"ID: {node.id} | {node.observation}\n" for node in nodes)


def fmt_insights(nodes: Sequence[Insight]) -> str:
    return "\n".join(f"ID: {node.id} | {node.title}: {node.insight} | "
                     f"Context Insight Applies: {node.context}\n" for node in nodes)


def confidence_of(value: object) -> Optional[int]:
    """The integer in an observation's confidence, written as a number or inside a string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def models_of(config: LatticeConfig) -> dict[str, str]:
    return {"observer": config.observer_model, "insight": config.insight_model,
            "evidence": config.evidence_model, "format": config.format_model}


def new_build(config: LatticeConfig, params: LatticeParams, device_id: Optional[str],
              since: Optional[float], until: Optional[float]) -> LatticeBuild:
    """A build record for ``params`` with a fresh id, before any caption is read."""
    now = time.time()
    return LatticeBuild(id=new_build_id(now), device_id=device_id, since=since, until=until,
                        status="observing", created_at=now, updated_at=now,
                        models=models_of(config), params=params, sessions=[], captions=0)


async def gather_limited(awaitables: Sequence[Awaitable[T]], limit: int) -> list[Outcome[T]]:
    """Every awaitable's result or exception, in order, at most ``limit`` running at once."""
    semaphore = asyncio.Semaphore(max(1, limit))

    async def wrapped(awaitable: Awaitable[T]) -> T:
        async with semaphore:
            return await awaitable

    return list(await asyncio.gather(*(wrapped(a) for a in awaitables), return_exceptions=True))


class Runner:
    """The build in progress: its record, the store it saves to, and the model call it counts."""

    def __init__(self, build: LatticeBuild, lattices: Lattices, config: LatticeConfig,
                 call: ModelCall):
        self.build = build
        self.lattices = lattices
        self.config = config
        self.call = call
        self.stats: dict[str, StageStats] = {}

    def stage_stats(self, stage: str) -> StageStats:
        return self.stats.setdefault(stage, StageStats())

    async def ask(self, stage: str, model: str, prompt: str, schema: Optional[dict] = None) -> str:
        """One counted model call; raises what the call raises."""
        stats = self.stage_stats(stage)
        stats.calls += 1
        reply = await self.call(model, prompt, schema)
        stats.record(reply)
        return reply.text

    async def parse_json(self, stage: str, text: str, schema: dict) -> Any:
        """The JSON in ``text``; when there is none, the format model is asked to fix it to ``schema``."""
        try:
            return parse_model_json(text)
        except ValueError as exc:
            logger.warning("%s: reply is not JSON (%s); asking %s to fix it", stage, exc,
                           self.build.models["format"])
        fixed = await self.ask(stage, self.build.models["format"], fallback_prompt(text, schema),
                               schema)
        return parse_model_json(fixed)

    def finish_stage(self, stage: str, started: float, nodes: int) -> None:
        """Record the stage on the build, log it and save the record."""
        stats = self.stage_stats(stage)
        seconds = time.monotonic() - started
        self.build.node_counts.append(nodes)
        self.build.stage_seconds[stage] = round(seconds, 3)
        self.build.stage_calls[stage] = stats.calls
        self.build.stage_failures[stage] = stats.failures
        self.build.updated_at = time.time()
        self.lattices.save_build(self.build)
        tokens = "usage not reported"
        if stats.counted:
            tokens = (f"prompt tokens {stats.prompt_tokens} (mean {stats.prompt_tokens / stats.counted:.0f}), "
                      f"completion tokens {stats.completion_tokens} "
                      f"(mean {stats.completion_tokens / stats.counted:.0f}) over {stats.counted} replies")
        logger.info("%s: %d nodes, %d calls, %d failed, %.1fs, %s", stage, nodes, stats.calls,
                    stats.failures, seconds, tokens)

    async def observe(self, sessions: Sequence[Sequence[Interaction]]) -> list[Observation]:
        """Layer 0: every window of every session read by the observer."""
        params = self.build.params
        name = params.user_name
        description = params.description.replace("{user_name}", name)
        windows = [(index, session[start:start + params.window_size])
                   for index, session in enumerate(sessions)
                   for start in range(0, len(session), params.window_size)]
        logger.info("observe: %d windows over %d sessions", len(windows), len(sessions))
        replies = await gather_limited(
            [self.ask(OBSERVE_STAGE, self.build.models["observer"],
                      OBSERVE_PROMPT.format(interaction_description=description, user_name=name,
                                            interactions=window_text(window)))
             for _, window in windows], self.config.max_concurrency)
        observations: list[Observation] = []
        stats = self.stage_stats(OBSERVE_STAGE)
        schema = ObservationsReply.model_json_schema()
        for (session_index, window), reply in zip(windows, replies):
            label = f"session {session_index} window at {window[0].time}"
            if isinstance(reply, BaseException):
                logger.error("observe: %s failed, skipped: %s", label, reply)
                stats.failures += 1
                continue
            try:
                parsed = await self.parse_json(OBSERVE_STAGE, reply, schema)
            except Exception as exc:
                logger.error("observe: %s has no observations to read, skipped: %s", label, exc)
                stats.failures += 1
                continue
            if not isinstance(parsed, dict) or not isinstance(parsed.get("observations"), list):
                logger.error("observe: %s reply has no observations list, skipped", label)
                stats.failures += 1
                continue
            for item in parsed["observations"]:
                if not isinstance(item, dict):
                    continue
                confidence = confidence_of(item.get("confidence"))
                if confidence is None:
                    logger.warning("observe: %s observation without a confidence, skipped", label)
                    continue
                for text in OBSERVATION_TEXTS:
                    if text in item:
                        observations.append(Observation(
                            id=len(observations), observation=str(item[text]), confidence=confidence,
                            metadata=NodeMetadata(input_session=session_index,
                                                  time=sessions[session_index][0].time)))
        return observations

    async def first_layer(self, spec: LayerSpec, observations: Sequence[Observation],
                          stage: str) -> InsightLayer:
        """Layer 1: observations grouped by ``spec``, each group written up as insights with their evidence."""
        params = self.build.params
        groups = split(spec, observations)
        stats = self.stage_stats(stage)
        logger.info("%s: %d groups of observations (%s)", stage, len(groups), spec.model_dump())
        prose = await gather_limited(
            [self.ask(stage, self.build.models["insight"], OBSERVATION_TO_INSIGHT_PROMPT.format(
                user_name=params.user_name, observations=fmt_observations(group),
                limit=params.min_insights))
             for group in groups], self.config.max_concurrency)
        written: list[tuple[int, str]] = []
        for index, result in enumerate(prose):
            if isinstance(result, BaseException):
                logger.error("%s: insights for group %d failed, skipped: %s", stage, index, result)
                stats.failures += 1
            else:
                written.append((index, result))

        schema = InsightsReply.model_json_schema()
        formatted = await gather_limited(
            [self.ask(stage, self.build.models["format"], FORMAT_INSIGHT_PROMPT.format(insights=raw),
                      schema)
             for _, raw in written], self.config.max_concurrency)
        insights: list[Insight] = []
        for (index, _), result in zip(written, formatted):
            if isinstance(result, BaseException):
                logger.error("%s: formatting group %d failed, skipped: %s", stage, index, result)
                stats.failures += 1
                continue
            try:
                reply = InsightsReply.model_validate(parse_model_json(result))
            except (ValueError, ValidationError) as exc:
                logger.error("%s: formatted insights of group %d unreadable, skipped: %s", stage,
                             index, exc)
                stats.failures += 1
                continue
            for item in reply.insights:
                insights.append(Insight(
                    id=len(insights), title=item.title, tagline=item.tagline, insight=item.insight,
                    context=item.context, supporting_evidence=item.supporting_evidence,
                    metadata=NodeMetadata(input_session=index, time=groups[index][-1].metadata.time)))

        evidence_schema = SupportingIdsReply.model_json_schema()
        mapped = await gather_limited(
            [self.ask(stage, self.build.models["evidence"], MAP_EVIDENCE_PROMPT.format(
                observations=fmt_observations(groups[insight.metadata.input_session]),
                evidence=insight.supporting_evidence), evidence_schema)
             for insight in insights], self.config.max_concurrency)
        edges: list[Edge] = []
        for insight, result in zip(insights, mapped):
            if isinstance(result, BaseException):
                logger.warning("%s: evidence of insight %d failed, left without edges: %s", stage,
                               insight.id, result)
                stats.failures += 1
                continue
            try:
                ids = SupportingIdsReply.model_validate(parse_model_json(result)).supporting_ids
            except (ValueError, ValidationError) as exc:
                logger.warning("%s: evidence of insight %d unreadable, left without edges: %s",
                               stage, insight.id, exc)
                stats.failures += 1
                continue
            insight.merged = ids
            edges.extend(Edge(source=insight.id, target=target) for target in ids)
        return InsightLayer(spec=spec, insights=insights, edges=edges)

    async def next_layer(self, spec: LayerSpec, below: Sequence[Insight], stage: str) -> InsightLayer:
        """A layer above 1: the insights below grouped by ``spec``, each group synthesized."""
        params = self.build.params
        groups = split(spec, below)
        stats = self.stage_stats(stage)
        logger.info("%s: %d groups of insights (%s)", stage, len(groups), spec.model_dump())
        replies = await gather_limited(
            [self.ask(stage, self.build.models["insight"], INSIGHT_SYNTHESIS_PROMPT.format(
                user_name=params.user_name, insights=fmt_insights(group), limit=params.min_insights))
             for group in groups], self.config.max_concurrency)
        schema = InsightsReply.model_json_schema()
        insights: list[Insight] = []
        for index, reply in enumerate(replies):
            if isinstance(reply, BaseException):
                logger.error("%s: synthesis of group %d failed, skipped: %s", stage, index, reply)
                stats.failures += 1
                continue
            try:
                parsed = await self.parse_json(stage, reply, schema)
                items = parsed["insights"]
                if not isinstance(items, list):
                    raise ValueError("insights is not a list")
            except Exception as exc:
                logger.error("%s: synthesis of group %d unreadable, skipped: %s", stage, index, exc)
                stats.failures += 1
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    insights.append(Insight.model_validate({
                        **item, "id": len(insights),
                        "metadata": {"input_session": index, "time": groups[index][-1].metadata.time}}))
                except ValidationError as exc:
                    logger.warning("%s: an insight of group %d is missing fields, skipped: %s",
                                   stage, index, exc)
        edges = [Edge(source=insight.id, target=target)
                 for insight in insights for target in insight.merged]
        return InsightLayer(spec=spec, insights=insights, edges=edges)


async def build_lattice(build: LatticeBuild, captions: ActivityCaptions, conversations: Conversations,
                        lattices: Lattices, config: LatticeConfig,
                        call: Optional[ModelCall] = None) -> Lattice:
    """Run the build: read the events of the types ``build.params.events`` names, observe, build
    each layer, saving ``build`` through ``lattices`` as each stage ends and the lattice when
    done. A failure is recorded on the build and raised.

    ``call`` sends each prompt; by default ``lattice.llm.litellm_call(config)``.
    """
    runner = Runner(build, lattices, config, litellm_call(config) if call is None else call)
    params = build.params
    try:
        build.status = "observing"
        build.updated_at = time.time()
        lattices.save_build(build)
        started = time.monotonic()
        sessions: list[list[Interaction]] = []
        if ACTIVITY in params.events:
            read = [c for c in captions.all(device_id=build.device_id, since=build.since, until=build.until)
                    if c.status == "done" and c.annotation is not None]
            sessions.extend(sessionize(interactions_of(read), params.session_gap_hours))
            build.captions = len(read)
        if MESSAGE in params.events:
            threads = conversations.all(device_id=build.device_id, since=build.since, until=build.until)
            sessions.extend(conversation_sessions(threads, params.user_name, params.turn_chars))
            build.conversations = len(threads)
            build.turns = sum(len(thread.turns) for thread in threads)
        sessions.sort(key=lambda session: session[0].source.time)
        build.sessions = source_sessions(sessions)
        lattices.save_build(build)
        logger.info("read %d captions and %d conversations (%d turns): %d interactions in %d sessions",
                    build.captions, build.conversations, build.turns, sum(len(s) for s in sessions),
                    len(sessions))
        observations = await runner.observe(sessions)
        runner.finish_stage(OBSERVE_STAGE, started, len(observations))

        build.status = "building"
        layers: list[InsightLayer] = []
        for number, spec in enumerate(build.params.layers, start=1):
            stage = f"layer_{number}"
            started = time.monotonic()
            if number == 1:
                layer = await runner.first_layer(spec, observations, stage)
            else:
                layer = await runner.next_layer(spec, layers[-1].insights, stage)
            layers.append(layer)
            runner.finish_stage(stage, started, len(layer.insights))

        build.status = "done"
        build.finished_at = build.updated_at = time.time()
        lattice = Lattice(build=build, observations=observations, layers=layers)
        lattices.save(lattice)
        return lattice
    except Exception as exc:
        build.status = "failed"
        build.error = f"{type(exc).__name__}: {exc}"
        build.updated_at = time.time()
        lattices.save_build(build)
        raise
