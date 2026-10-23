"""Jev's answers to the questions in ``questions.json``, one record per captioned chunk.

A chunk's record is made once every row of it has an answer or an error that
asking again would not fix. It names the model, the version of
``questions.json`` it answers and the tokens it cost, so answers from two models
or two versions of the questions can be compared later.

``QuestionAnswers`` is what the server reads and writes through;
``FileQuestionAnswers`` keeps chunk ``n`` of a session in
``<session dir>/question_answers/<n as four digits>.json``.
"""

import logging
from pathlib import Path
from typing import Annotated, Hashable, Literal, Optional, Protocol, Union

from pydantic import BaseModel, Field, ValidationError

from juno.sessions import dir_stamps, replace_file, session_dir

logger = logging.getLogger(__name__)

ANSWERS_DIR = "question_answers"


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: dict[str, float]


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float
    confidence: float
    # Level number -> probability.
    probabilities: dict[int, float]


Answer = Annotated[Union[NoulAnswer, ChoiceAnswer, ScoreAnswer], Field(discriminator="type")]


class RowAnswers(BaseModel):
    # Question id -> answer; an idle row has no answers to ``action`` questions.
    answers: dict[str, Answer] = {}
    input_tokens: Optional[int] = None
    # Why the row has no answers.
    error: Optional[str] = None


class ChunkAnswers(BaseModel):
    session_id: str
    chunk: int
    model: str
    # ``QuestionSet.version`` of the questions answered.
    questions: str
    # ``updated_at`` of the activity caption whose captions were sent; a newer one is asked again.
    caption_updated_at: float
    created_at: float
    # By row of the session.
    rows: dict[int, RowAnswers]


class QuestionAnswers(Protocol):

    def get(self, session_id: str, chunk: int) -> Optional[ChunkAnswers]:
        """The chunk's answers; None if there are none or they cannot be read."""

    def save(self, answers: ChunkAnswers) -> None:
        """Store the chunk's answers, replacing earlier ones."""

    def version(self, session_id: str) -> Hashable:
        """A value that differs whenever any answers of the session do."""

    def by_row(self, session_id: str) -> dict[int, dict[str, Answer]]:
        """Every answered row of a session, by row."""


def answers_file(directory: Path, chunk: int) -> Path:
    return directory / ANSWERS_DIR / f"{chunk:04d}.json"


class FileQuestionAnswers:

    def __init__(self, devices_dir: Path):
        self.devices_dir = devices_dir

    def get(self, session_id: str, chunk: int) -> Optional[ChunkAnswers]:
        path = answers_file(session_dir(self.devices_dir, session_id), chunk)
        try:
            return ChunkAnswers.model_validate_json(path.read_text())
        except FileNotFoundError:
            return None
        except ValidationError as exc:
            logger.warning("skipping %s: %s", path, exc)
            return None

    def save(self, answers: ChunkAnswers) -> None:
        replace_file(answers_file(session_dir(self.devices_dir, answers.session_id), answers.chunk),
                     answers.model_dump_json(indent=2))

    def version(self, session_id: str) -> Hashable:
        return dir_stamps(session_dir(self.devices_dir, session_id) / ANSWERS_DIR)

    def by_row(self, session_id: str) -> dict[int, dict[str, Answer]]:
        answered = {}
        directory = session_dir(self.devices_dir, session_id) / ANSWERS_DIR
        for path in sorted(directory.glob("*.json")):
            try:
                record = ChunkAnswers.model_validate_json(path.read_text())
            except (OSError, ValidationError) as exc:
                logger.warning("skipping %s: %s", path, exc)
                continue
            for row, answers in record.rows.items():
                if answers.answers:
                    answered[row] = answers.answers
        return answered
