"""Dawn's steps (Zhao et al., arXiv 2604.07629, Sec. 4 and Appendix A.6-A.9): from one day's
action captions to the tasks a personal agent could help with, and two actions per task.

The prompts are the paper's, character for character where the appendix prints
them, with ``{user_name}`` where the paper prints ``USER`` and the bracketed
inputs filled in. The paper prints no reply format for them; the JSON each
step is asked for is in the ``*Reply`` models, passed as the reply's schema.

The steps, in order, over the lattice's top layer of insights:

1. ``induce_tasks``: the day's action captions, as the captions' actions
   (the paper's "list of low-level actions"), to the high-level tasks of the
   day (A.8, the task model). With ``dawn.actions_per_window`` set, the day is
   read in windows of that many captions and the tasks of all windows are put
   to the model once more, as the list of goals the prompt asks it to choose
   from, so a day fits a model with a short context.
2. ``score_utility``: each task, with every insight, to a 0-1 estimate of how
   much a personal agent knowing the user would change the help (A.9, the
   utility model); tasks under ``dawn.utility_threshold`` are dropped.
3. ``pick_insights``: the ``dawn.insights_per_task`` insights ranked most
   relevant to the task by the cross encoder (``lattice.rerank``).
4. ``propose_actions``: the task, those insights and the agent's constraints
   (``dawn.constraints``, the paper's A.7 for a general-purpose harness) to two
   actions (A.6, the action model), with the candidates it ideated and did not
   select.

``lattice.proposals`` runs these over one day and keeps the result.

Every function that calls a model takes the ``ModelCall`` of ``lattice.llm``;
the rest is pure.
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, Field

from annotate.store import ActivityCaption
from lattice.llm import ModelCall, Reply
from lattice.models import Insight
from lattice.parse import parse_model_json

INDUCE_TASKS_PROMPT = """You are provided with a list of goals for the user {user_name}.
Your task is to select goals where a user could use the assistance of an AI agent to complete.

# Rubric
When selecting goals, consider the following criteria when selecting goals:
1. The goal is not something that the user can easily complete on their own.
2. The goal is open-ended in nature and requires the user to think critically and creatively.
3. The goal is not something that the user can easily complete in a single step.
4. The goal should cover one project / task.
5. The outcome of the goal is subjective in nature (i.e., not something that can simply be marked as "done" or "not done").

Remember the goal should list the **high-level activity** that the user is working on. Do NOT enumerate the specific tasks they are doing to complete the goal.

# Input
As input, you are provided with a list of actions for the user {user_name}.

{actions}
"""

CLASSIFY_UTILITY_PROMPT = """You are given a TASK and USER INFORMATION.

Your job is to estimate how much a PERSONAL AGENT with this USER INFORMATION would improve the handling of the TASK over a generic AI assistant with no user context.

# DEFINITION
Your job is to estimate how DIFFERENT the resulting response would be if a PERSONAL AGENT with deep knowledge of the user handled the task instead of a generic AI assistant with no user context. A PERSONAL AGENT has detailed insight into the user's cognitive patterns, personality traits, working habits, recurring struggles, motivations, and emotional dynamics.

Scoring guidelines:
0.0 to 0.2
Generic AI assistance is sufficient. Personal knowledge of the user would not meaningfully change the response.

0.3 to 0.6
Personal knowledge could somewhat improve the response but is not critical.

0.7 to 1.0
Deep understanding of the user would significantly change the response or recommendations.

The rating will be used by a system that activates a PERSONAL AGENT when the rating exceeds a threshold. Be conservative in your rating.

# INPUT
[TASK]
{task}

[INSIGHTS]
{insights}
"""

PROPOSE_ACTIONS_PROMPT = """You are given the task that the user is working on and relevant USER INSIGHTS.

Your job is to propose 2 actions that a tool-calling agent can take to assist the user.

# Guidelines
1. Review the user insights and task. Reason about how the user insights reframe the actions needed to address the task (e.g., as HMW questions from design). Explicitly list out these reframings as part of the reasoning process.
2. Ideate a wide range of potential actions the AI agent can take based on the insights and task. Assume that the AI agent can gather the necessary context about the user.
3. For each action, evaluate the action given the USER INSIGHTS. Rank the actions by how much the action would benefit {user_name}.
4. Next, for each action, check its implementability given the IMPLEMENTATION CONSTRAINTS. For each action, verify whether it can implemented under these constraints. Remove any actions that are not implementable.
5. Select the top 2 actions based on what we know about {user_name}.

# Input
[TASK]
{task}

[INSIGHTS]
{insights}

[IMPLEMENTATION CONSTRAINTS]
{constraints}
"""

# How a reply's task, score or action is asked for; the paper prints none.
TASK_REPLY_FORMAT = """
# Output
Return your results only in this exact JSON format:
{{"tasks": [{{"task": "<the high-level activity, one sentence>", "reasoning": "<why it meets the rubric, naming the actions it rests on>"}}]}}
"""

UTILITY_REPLY_FORMAT = """
# Output
Return your results only in this exact JSON format:
{{"score": <0.0 to 1.0>, "reasoning": "<1-2 sentences>"}}
"""

ACTIONS_REPLY_FORMAT = """
# Output
Return your results only in this exact JSON format:
{{"reframings": ["<HMW question>", ...], "actions": [{{"title": "<a few words>", "action": "<what the agent does, 2-4 sentences>", "insight_ids": [<ids of the insights it draws on>], "benefit": "<why it helps {user_name}, 1-2 sentences>"}}], "considered": [{{"title": "<an action you ideated and did not select>", "why": "<why it ranked lower or was not implementable, one sentence>"}}]}}
"""


class TaskReply(BaseModel):
    task: str
    reasoning: str = ""


class TasksReply(BaseModel):
    tasks: list[TaskReply]


class UtilityReply(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class ActionReply(BaseModel):
    title: str
    action: str
    insight_ids: list[int] = []
    benefit: str = ""


class Considered(BaseModel):
    """An action the proposer ideated and did not select, and why."""
    title: str
    why: str = ""


class ActionsReply(BaseModel):
    reframings: list[str] = []
    actions: list[ActionReply]
    considered: list[Considered] = []


class ScoredTask(BaseModel):
    task: str
    reasoning: str
    # ``UtilityReply.score``; None when the call failed.
    score: Optional[float]
    utility_reasoning: str = ""


class ProposedActions(BaseModel):
    task: ScoredTask
    # The insights the action model saw, best ranked first, by id in the top layer.
    insight_ids: list[int]
    reframings: list[str]
    actions: list[ActionReply]
    # What the action model ideated and ranked below the actions, or could not implement.
    considered: list[Considered] = []
    error: Optional[str] = None


def day_actions(captions: list[ActivityCaption]) -> list[str]:
    """The action captions of done captions, in caption and action order, each with its time."""
    lines = []
    for caption in sorted(captions, key=lambda c: (c.start_time or 0.0, c.chunk)):
        if caption.status != "done" or caption.annotation is None:
            continue
        for action in caption.annotation.actions:
            frame = caption.frames[action.start - 1] if 0 < action.start <= len(caption.frames) else None
            when = frame.timestamp if frame is not None and frame.timestamp is not None else caption.start_time
            clock = "" if when is None else time.strftime("%H:%M", time.localtime(when))
            lines.append(f"{clock} | {action.caption}")
    return lines


def windows(actions: list[str], size: int) -> list[list[str]]:
    """``actions`` in windows of ``size``; one window of all of them when ``size`` is 0."""
    if size <= 0:
        return [actions]
    return [actions[start:start + size] for start in range(0, len(actions), size)]


def format_goals(tasks: list[TaskReply]) -> str:
    return "\n".join(f"- {task.task}" for task in tasks)


def format_actions(actions: list[str]) -> str:
    return "\n".join(f"- {line}" for line in actions)


def format_insights(insights: list[Insight]) -> str:
    """Insights as ``lattice.build`` formats them for the synthesis prompt, one per line."""
    return "\n".join(f"ID: {i.id} | {i.title}: {i.insight} | Context Insight Applies: {i.context}\n"
                     for i in insights)


def insight_document(insight: Insight) -> str:
    """The text of an insight the reranker scores against a task."""
    return f"{insight.title}. {insight.tagline} {insight.insight} {insight.context}"


def parse_reply(reply: Reply, model: type[BaseModel]) -> BaseModel:
    return model.model_validate(parse_model_json(reply.text))


async def induce_from(call: ModelCall, model: str, user_name: str, listing: str) -> list[TaskReply]:
    """A.8 over one listing of actions, or of goals already induced."""
    prompt = INDUCE_TASKS_PROMPT.format(user_name=user_name, actions=listing) + TASK_REPLY_FORMAT.format()
    reply = await call(model, prompt, TasksReply.model_json_schema())
    return parse_reply(reply, TasksReply).tasks


async def induce_tasks(call: ModelCall, model: str, user_name: str, actions: list[str],
                       actions_per_window: int = 0) -> list[TaskReply]:
    """A.8: the day's high-level tasks from its action captions, by window and then merged."""
    parts = windows(actions, actions_per_window)
    tasks = []
    for part in parts:
        tasks.extend(await induce_from(call, model, user_name, format_actions(part)))
    if len(parts) > 1:
        tasks = await induce_from(call, model, user_name, format_goals(tasks))
    return tasks


async def score_utility(call: ModelCall, model: str, task: TaskReply,
                        insights: list[Insight]) -> ScoredTask:
    """A.9: how much a personal agent with the insights would change the help with ``task``."""
    prompt = CLASSIFY_UTILITY_PROMPT.format(task=task.task, insights=format_insights(insights)) \
        + UTILITY_REPLY_FORMAT.format()
    reply = await call(model, prompt, UtilityReply.model_json_schema())
    utility = parse_reply(reply, UtilityReply)
    return ScoredTask(task=task.task, reasoning=task.reasoning, score=utility.score,
                      utility_reasoning=utility.reasoning)


def keep(tasks: list[ScoredTask], threshold: float) -> list[ScoredTask]:
    """The tasks scoring at least ``threshold``."""
    return [task for task in tasks if task.score is not None and task.score >= threshold]


async def propose_actions(call: ModelCall, model: str, user_name: str, task: ScoredTask,
                          insights: list[Insight], constraints: str) -> ProposedActions:
    """A.6: two actions for ``task`` given its best insights and what the agent can do."""
    prompt = PROPOSE_ACTIONS_PROMPT.format(user_name=user_name, task=task.task,
                                           insights=format_insights(insights),
                                           constraints=constraints) \
        + ACTIONS_REPLY_FORMAT.format(user_name=user_name)
    reply = await call(model, prompt, ActionsReply.model_json_schema())
    actions = parse_reply(reply, ActionsReply)
    return ProposedActions(task=task, insight_ids=[i.id for i in insights],
                           reframings=actions.reframings, actions=actions.actions,
                           considered=actions.considered)
