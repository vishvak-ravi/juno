"""Jev's answers on disk: one JSON file per annotated chunk of a session.

The answers to chunk ``n`` are stored in
``<session dir>/detections/jev/<n as four digits>.json``, with the model, the
version of ``questions.json`` they answer and the tokens they cost, so answers
to two models or two versions of the questions can be compared later. A
chunk's file is written once every row of it has an answer or an error that
asking again would not fix.
"""

import logging
import os
import threading
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

DETECTIONS_DIR = "detections"
DETECTOR = "jev"


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


class RowDetection(BaseModel):
    # Question id -> answer; an idle row has no answers to ``action`` questions.
    answers: dict[str, Answer] = {}
    input_tokens: Optional[int] = None
    # Why the row has no answers.
    error: Optional[str] = None


class DetectionRecord(BaseModel):
    session_id: str
    chunk: int
    model: str
    # ``QuestionSet.version`` of the questions answered.
    questions: str
    # ``updated_at`` of the annotation whose captions were sent; a newer one is asked again.
    annotation_updated_at: float
    created_at: float
    # By row of the session.
    rows: dict[int, RowDetection]


def detection_file(session_dir: Path, chunk: int) -> Path:
    return session_dir / DETECTIONS_DIR / DETECTOR / f"{chunk:04d}.json"


def read_detection(session_dir: Path, chunk: int) -> Optional[DetectionRecord]:
    """The stored answers of a chunk; None if there are none or the file is unreadable."""
    path = detection_file(session_dir, chunk)
    try:
        return DetectionRecord.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None
    except ValidationError as exc:
        logger.warning("skipping %s: %s", path, exc)
        return None


def write_detection(session_dir: Path, record: DetectionRecord) -> None:
    """Replace the file in one step, so a reader never sees half a record."""
    path = detection_file(session_dir, record.chunk)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
    temporary.write_text(record.model_dump_json(indent=2))
    os.replace(temporary, path)


def session_answers(session_dir: Path) -> dict[int, dict[str, Answer]]:
    """Every answered row of a session, by row."""
    answers = {}
    for path in sorted((session_dir / DETECTIONS_DIR / DETECTOR).glob("*.json")):
        try:
            record = DetectionRecord.model_validate_json(path.read_text())
        except (OSError, ValidationError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        for row, detection in record.rows.items():
            if detection.answers:
                answers[row] = detection.answers
    return answers
