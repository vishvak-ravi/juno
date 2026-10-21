"""Run a next-action benchmark: predict each sample of a dataset, then score it with LongNAP's reward.

    uv run python scripts/benchmark_prediction.py CONFIG.yaml

The dataset comes from ``scripts/make_prediction_dataset.py``. Each prediction
is scored by ``predict.reward``: Tada's count check, then the ``grade.judge``
accuracy and formatting judges against the actions that actually followed.

Each sample's row is written next to the session's events, in
``<session dir>/benchmarks/<run>.jsonl`` (``predict.benchmarks.GradedRow``): the
predictor and grader, what each model was sent and what it replied, and the
scores. ``GET /predictions`` lists them.

Where Tada would retry forever (online) or score 0 (offline) when a model call
fails, a call here is repeated while the provider is busy, up to
``max_attempts``; a sample whose calls still fail is recorded with its error
and left out of the means.

The config (YAML):

    dataset: path/to/dataset.jsonl
    predictor:                  # the intent model under test
      model: openai/<served model name>         # any litellm model
      api_base: http://localhost:8000/v1        # optional
      max_tokens: 2048
      temperature: 1.0
    grader:                     # optional; default: the grade section of config.yaml
      model: gemini/gemini-3.1-flash-lite
    limit: null                 # predict only the first N samples
    max_attempts: 5             # predictor calls while the provider is busy
    retry_seconds: 60           # wait between them when the provider names none

Keys come from the environment or ``.env`` (GEMINI_API_KEY for gemini/).
Where sessions are stored comes from ``capture.data_dir`` in ``config.yaml``
(or JUNO_CONFIG_PATH).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, TextIO
from urllib.parse import unquote, urlparse

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from grade.call import CallFailed, complete, judge_for
from juno.config import ConfigError, Section, devices_dir, load_config, resolve_data_dir
from juno.sessions import session_dir
from predict.benchmarks import (RUN_FORMAT, GradedRow, GraderSettings, PredictorSettings,
                                run_file)
from predict.predictor import image_part
from predict.reward import reward

ROOT_DIR = Path(__file__).resolve().parents[1]


class BenchmarkConfig(Section):
    dataset: Path
    predictor: PredictorSettings
    grader: Optional[GraderSettings] = None
    limit: Optional[int] = None
    max_attempts: int = 5
    retry_seconds: float = 60.0


def with_images(messages: list[dict]) -> list[dict]:
    """``messages`` with each ``file://`` screenshot replaced by the image itself."""
    out = []
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            content = [image_part(unquote(urlparse(part["image_url"]["url"]).path))
                       if part.get("type") == "image_url"
                       and part["image_url"]["url"].startswith("file://") else part
                       for part in content]
        out.append({**message, "content": content})
    return out


def mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path, help="the benchmark config (YAML)")
    args = parser.parse_args(argv)

    load_dotenv(ROOT_DIR / ".env")
    try:
        config = BenchmarkConfig.model_validate(yaml.safe_load(args.config.read_text()) or {})
        lines = config.dataset.read_text().splitlines()
        app_config = load_config(None if os.environ.get("JUNO_CONFIG_PATH")
                                 else ROOT_DIR / "config.yaml")
    except (OSError, yaml.YAMLError, ValidationError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    samples = [json.loads(line) for line in lines if line.strip()][:config.limit]
    store = devices_dir(resolve_data_dir(app_config.capture))
    run = time.strftime(RUN_FORMAT)

    grade_config = app_config.grade
    if config.grader is not None:
        grade_config = grade_config.model_copy(update=config.grader.model_dump())
    grader = GraderSettings(model=grade_config.model, api_base=grade_config.api_base)
    judge = judge_for(grade_config)

    # One file per session the samples come from.
    files: dict[str, TextIO] = {}
    rows: list[GradedRow] = []
    try:
        for index, sample in enumerate(samples):
            fields = {}
            try:
                fields["prediction"] = complete(
                    config.predictor.model, config.predictor.api_base,
                    with_images(sample["messages"]), config.max_attempts, config.retry_seconds,
                    max_tokens=config.predictor.max_tokens,
                    temperature=config.predictor.temperature)
                fields.update(reward(fields["prediction"], sample["ground_truth"], judge))
            except (CallFailed, OSError) as exc:
                fields = {"prediction": fields.get("prediction"), "error": str(exc)}
            row = GradedRow(
                session_id=sample["session_id"], run=run, index=index, graded_at=time.time(),
                dataset=str(config.dataset.resolve()), predictor=config.predictor,
                grader=grader, cutoff_ts=sample.get("cutoff_ts"),
                future_len=sample["future_len"], messages=sample["messages"],
                retrieved=sample.get("retrieved", ""), ground_truth=sample["ground_truth"],
                **fields)
            rows.append(row)
            if row.session_id not in files:
                path = run_file(session_dir(store, row.session_id), run)
                path.parent.mkdir(parents=True, exist_ok=True)
                files[row.session_id] = open(path, "x")
            files[row.session_id].write(row.model_dump_json() + "\n")
            files[row.session_id].flush()
            status = row.error or (f"reward {row.reward:.2f} (accuracy {row.accuracy:.2f}, "
                                   f"formatting {row.formatting:.2f})")
            print(f"[{index + 1}/{len(samples)}] {status}", file=sys.stderr)
    finally:
        for file in files.values():
            file.close()

    scored = [row for row in rows if row.error is None]
    summary = {
        "predictor": config.predictor.model, "grader": grader.model,
        "dataset": str(config.dataset), "run": run, "samples": len(samples),
        "scored": len(scored), "failed": len(rows) - len(scored),
        "invalid": sum(1 for row in scored if row.validation_errors),
        "unparsed_judge_replies": sum(len(row.unparsed_replies) for row in scored),
        "reward": mean([row.reward for row in scored]),
        "accuracy": mean([row.accuracy for row in scored]),
        "formatting": mean([row.formatting for row in scored]),
        "results": [file.name for file in files.values()],
    }
    print(json.dumps(summary, indent=2))
    return 0 if scored else 1


if __name__ == "__main__":
    sys.exit(main())
