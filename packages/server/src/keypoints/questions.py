"""The questions every row is asked, and what the answers become.

``questions.json`` holds two groups of TypeSafe questions, keyed by question
id: ``screen`` is asked about every row, ``action`` only about a row with input
(``active``). Its ``indices`` name the questions read as a number per row,
``moments`` the questions read as a divider in the timeline, and ``labels`` the
Choice that names a moment's subject when another question says it applies.
"""

import hashlib
from importlib.resources import files
from typing import Any

from pydantic import BaseModel

from juno.sessions import Aggregation

# Inputs that say the user did something in a row; releases and pointer moves don't.
ACTIVE_INPUTS = frozenset({"key_press", "mouse_down", "mouse_scroll"})


class Label(BaseModel):
    # The Choice whose option names the moment's subject.
    choice: str
    # The Noul that must reach ``keypoints.moment_on`` for the Choice to be read.
    when: str


class Moments(BaseModel):
    # Something that stays on screen: a moment where it first appears.
    state: list[str]
    # Something the user does: a moment on every row it happens in.
    action: list[str]


class QuestionSet(BaseModel):
    screen: dict[str, dict[str, Any]]
    action: dict[str, dict[str, Any]]
    # Index name -> question id.
    indices: dict[str, str]
    moments: Moments
    # Moment type -> its label.
    labels: dict[str, Label] = {}
    # The first characters of the file's SHA-256, stored with every answer.
    version: str = ""



def load_questions() -> QuestionSet:
    text = (files("keypoints") / "questions.json").read_text()
    questions = QuestionSet.model_validate_json(text)
    return questions.model_copy(update={"version": hashlib.sha256(text.encode()).hexdigest()[:12]})


def active(aggregation: Aggregation) -> bool:
    """Whether the user pressed a key, clicked or scrolled in the row."""
    return any(event.event_type in ACTIVE_INPUTS for event in aggregation.events)
