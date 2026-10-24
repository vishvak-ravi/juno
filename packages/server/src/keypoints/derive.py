"""What the stored answers become when read: a number per index and the moments of a session.

Nothing here asks a model, so changing a threshold changes what is shown
without asking Jev again.
"""

from typing import Optional

from pydantic import BaseModel

from keypoints.questions import QuestionSet
from keypoints.store import Answer, ChoiceAnswer, NoulAnswer, ScoreAnswer

# ``Moment.detector`` of the moments found in Jev's answers.
DETECTOR = "jev"


class Conversation(BaseModel):
    """The assistant conversation a moment happened in, as its own hook reported it."""
    # The ``agent_harness`` option of the assistant, e.g. ``claude_code``.
    harness: str
    # The assistant's id of the conversation, e.g. what ``claude --resume`` takes.
    session_id: str
    # The directory the conversation runs in, on the device of the moment's event.
    cwd: str
    # Where the conversation runs, as Claude Code's CLAUDE_CODE_ENTRYPOINT names it: ``cli`` in a
    # terminal, ``claude-desktop`` in the Claude desktop app.
    entrypoint: str
    # The Claude desktop app's own id of the session (its CLAUDE_CODE_HOST_SESSION_ID), what its
    # ``claude://code/continue?session=`` link takes; None for a terminal's.
    host_session_id: Optional[str] = None


class Moment(BaseModel):
    # A moment question id of ``questions.json``.
    type: str
    probability: float
    # What produced it: ``jev``, or an assistant's hook such as ``claude_code_hook``.
    detector: str
    # The option of the moment's label Choice, e.g. which assistant; None without one.
    label: Optional[str] = None
    # Set on moments an assistant's hook reported; None on Jev's.
    conversation: Optional[Conversation] = None


def probability(answer: Answer) -> float:
    """A Noul's probability of yes, or a Score's position as a fraction of its top level."""
    if isinstance(answer, NoulAnswer):
        return answer.noul
    if isinstance(answer, ScoreAnswer):
        top = max(answer.probabilities, default=0)
        return answer.score / top if top else 0.0
    raise ValueError(f"a {answer.type} answer has no single probability")


def row_indices(answers: dict[str, Answer], questions: QuestionSet) -> dict[str, float]:
    """Index name -> 0..1 for one row; an index whose question went unanswered is left out."""
    return {name: probability(answers[question_id])
            for name, question_id in questions.indices.items() if question_id in answers}


def label_of(moment_type: str, answers: dict[str, Answer], questions: QuestionSet,
             moment_on: float) -> Optional[str]:
    label = questions.labels.get(moment_type)
    if label is None:
        return None
    when, choice = answers.get(label.when), answers.get(label.choice)
    if when is None or not isinstance(choice, ChoiceAnswer) or probability(when) < moment_on:
        return None
    return choice.choice


def session_moments(rows: dict[int, dict[str, Answer]], questions: QuestionSet,
                    moment_on: float, moment_off: float) -> dict[int, list[Moment]]:
    """The moments of one session's answered rows, by row.

    An ``action`` moment is every row whose probability is at least
    ``moment_on``. A ``state`` moment is a row where it reaches ``moment_on``,
    and the same type starts again only after a row below ``moment_off``, so
    something left on screen for many rows is one moment. Rows without an
    answer to a question don't change its state.
    """
    moment_types = questions.moments.state + questions.moments.action
    armed = {moment_type: True for moment_type in questions.moments.state}
    moments: dict[int, list[Moment]] = {}
    for row in sorted(rows):
        answers = rows[row]
        for moment_type in moment_types:
            if moment_type not in answers:
                continue
            p = probability(answers[moment_type])
            state = moment_type in armed
            if state and p < moment_off:
                armed[moment_type] = True
            if p < moment_on or (state and not armed[moment_type]):
                continue
            if state:
                armed[moment_type] = False
            moments.setdefault(row, []).append(Moment(
                type=moment_type, probability=p, detector=DETECTOR,
                label=label_of(moment_type, answers, questions, moment_on)))
    return moments
