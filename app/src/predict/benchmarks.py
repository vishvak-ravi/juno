"""Benchmark datasets and graded predictions on disk, next to the session they come from.

A dataset made from a session (``scripts/make_dataset.py``) is
``<session dir>/datasets/<time>.jsonl``: one sample per line, the request the
predictor is sent and the actions that actually followed. A benchmark run
(``scripts/benchmark.py``) writes ``<session dir>/benchmarks/<run>.jsonl``: one
``GradedRow`` per sample, holding the predictor and grader that made it, what
each model was sent and what came back. ``<run>`` is the time the run started,
``YYYYmmdd_HHMMSS``.

Graded prediction ``<session id>-<run>-<index as four digits>`` is the row with
that ``index`` in that run's file.

Screenshots in the predictor's messages are ``file://`` URLs to the session's
own screenshots, not the images themselves.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from annotate.records import SESSION_ID_PATTERN, session_dirs, session_id_of
from juno.config import Section

logger = logging.getLogger(__name__)

DATASETS_DIR = "datasets"
BENCHMARKS_DIR = "benchmarks"
RUN_FORMAT = "%Y%m%d_%H%M%S"
GRADED_ID = re.compile(
    rf"^(?P<session_id>{SESSION_ID_PATTERN})-(?P<run>\d{{8}}_\d{{6}})-(?P<index>\d{{4,}})$")


class PredictorSettings(Section):
    # Any litellm model; ``hosted_vllm/<served name>`` with ``api_base`` for a local server.
    model: str
    api_base: Optional[str] = None
    # Tada's ``PromptedPredictor`` defaults.
    max_tokens: int = 2048
    temperature: float = 1.0


class GraderSettings(Section):
    # Tada's default reward_llm.
    model: str = "gemini/gemini-3.1-flash-lite"
    api_base: Optional[str] = None


class GradedRow(BaseModel):
    """One line of a benchmark run's file: one sample, predicted and graded."""
    session_id: str
    run: str
    # The sample's line in the dataset, from 0.
    index: int
    # Unix seconds.
    graded_at: float
    dataset: str
    predictor: PredictorSettings
    grader: GraderSettings
    # The end of the past the predictor was shown (Unix seconds), and how many
    # actions it was asked for.
    cutoff_ts: Optional[float]
    future_len: int
    # What the predictor was sent; ``retrieved`` is the earlier activity in it.
    messages: list[dict[str, Any]]
    retrieved: str
    ground_truth: str
    # None if the predictor call failed; ``error`` then says why.
    prediction: Optional[str]
    error: Optional[str] = None
    # Tada's reward: 0.5 * accuracy + 0.5 * formatting, or 0 if the prediction
    # failed ``validation_errors``. None if a model call failed.
    reward: Optional[float] = None
    accuracy: Optional[float] = None
    formatting: Optional[float] = None
    validation_errors: list[str] = []
    # Judge replies that were not the JSON asked for; each scored 0, as in Tada.
    unparsed_replies: list[str] = []
    # What each judge was sent and replied; None when it was not asked.
    accuracy_prompt: Optional[str] = None
    accuracy_reply: Optional[str] = None
    formatting_prompt: Optional[str] = None
    formatting_reply: Optional[str] = None


class GradedPrediction(GradedRow):
    """A ``GradedRow`` as the API returns it, with its id."""
    id: str
    device_id: str


def graded_id(session_id: str, run: str, index: int) -> str:
    return f"{session_id}-{run}-{index:04d}"


def datasets_dir(session_dir: Path) -> Path:
    return session_dir / DATASETS_DIR


def run_file(session_dir: Path, run: str) -> Path:
    return session_dir / BENCHMARKS_DIR / f"{run}.jsonl"


def read_run(path: Path) -> list[GradedPrediction]:
    """The rows of one run's file; lines that are not a ``GradedRow`` are skipped."""
    session_id = session_id_of(path.parent.parent)
    graded = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = GradedRow.model_validate_json(line)
        except ValidationError as exc:
            logger.warning("skipping %s line %d: %s", path, number, exc)
            continue
        graded.append(GradedPrediction(
            **row.model_dump(), id=graded_id(session_id, path.stem, row.index),
            device_id=session_id.split("-", 1)[0]))
    return graded


def list_graded(devices_dir: Path, device_id: Optional[str] = None) -> list[GradedPrediction]:
    """Every graded prediction of every run, of one device or of all, oldest first."""
    graded = [row for directory in session_dirs(devices_dir, device_id)
              for path in sorted((directory / BENCHMARKS_DIR).glob("*.jsonl"))
              for row in read_run(path)]
    return sorted(graded, key=lambda row: (row.graded_at, row.id))
