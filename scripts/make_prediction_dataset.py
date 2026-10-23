"""Make a next-action benchmark dataset from one annotated session.

    uv run python scripts/make_prediction_dataset.py SESSION_ID [--out PATH]

Each sample is a window of the session's annotated actions: the request Juno's
predictor (``predict.predictor``, Tada's ``PromptedPredictor``) sends for the
last ``predict.past_len`` actions, and the ``predict.future_len`` actions that
actually followed, as the ``<actions>`` block Tada's grader compares against.
Windows advance by ``future_len``, so the ground truths do not overlap and a
session of E actions gives (E - past_len) // future_len samples.

Two things differ from a live prediction, both so that a sample shows only what
was known at its cutoff:
- Actions are ordered and timed by the end of the chunk they were annotated
  from, not by when the annotation finished: the server annotates chunks out of
  order (backfill, concurrency).
- The retrieval index holds only the actions up to the end of the window's past.
  An index of the whole session would put later actions into the retrieved
  context, under the dense caption they share with earlier ones.

Screenshots are referenced by ``file://`` URL instead of embedded; the benchmark
script reads them when it sends the request. The dataset is written next to the
session's events, in ``<session dir>/datasets/`` (see ``predict.benchmarks``).
Settings come from the ``predict`` and ``capture`` sections of
``config.yaml`` (or JUNO_CONFIG_PATH).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from juno.sessions import session_dir
from annotate.store import FileActivityCaptions
from juno.config import ConfigError, devices_dir, load_config, resolve_data_dir
from predict.benchmarks import RUN_FORMAT, datasets_dir
from predict.predictor import Predictor, build_actions_block, prediction_events

ROOT_DIR = Path(__file__).resolve().parents[1]


def build_samples(session_id: str, events: list[dict], predictor_config) -> list[dict]:
    """One sample per non-overlapping future window of ``events`` (oldest first)."""
    past_len, future_len = predictor_config.past_len, predictor_config.future_len
    samples = []
    for start in range(0, len(events) - past_len - future_len + 1, future_len):
        past = events[start:start + past_len]
        future = events[start + past_len:start + past_len + future_len]
        predictor = Predictor(predictor_config)
        predictor.index_context(events[:start + past_len])
        messages, retrieved = predictor.build_messages(past)
        # The events whose screenshots ``build_messages`` put first, in its order.
        num_imgs = predictor_config.num_imgs_per_sample
        image_paths = [] if num_imgs is None else [
            event["img_path"] for event in past[-num_imgs:] if event.get("img_path") is not None]
        image_parts = [part for part in messages[0]["content"] if part["type"] == "image_url"]
        for part, path in zip(image_parts, image_paths, strict=True):
            part["image_url"]["url"] = Path(path).resolve().as_uri()
        samples.append({
            "session_id": session_id,
            "messages": messages,
            "ground_truth": build_actions_block(future),
            "future_len": future_len,
            "retrieved": retrieved,
            "cutoff_ts": past[-1]["timestamp"],
        })
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session_id", help="a stored session's id: <device id>-<session name>")
    parser.add_argument("--out", type=Path,
                        help="the JSONL file to write; must not exist. "
                             "Default: <session dir>/datasets/<time>.jsonl")
    args = parser.parse_args(argv)

    try:
        config = load_config(None if os.environ.get("JUNO_CONFIG_PATH")
                             else ROOT_DIR / "config.yaml")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data_dir = resolve_data_dir(config.capture)

    records = [record.model_copy(update={"updated_at": record.end_time})
               for record in FileActivityCaptions(devices_dir(data_dir)).all(session_id=args.session_id)
               if record.status == "done" and record.end_time is not None]
    events = prediction_events(records)
    needed = config.predict.past_len + config.predict.future_len
    if len(events) < needed:
        print(f"error: {args.session_id} has {len(events)} annotated actions; "
              f"a sample needs {needed}", file=sys.stderr)
        return 2

    samples = build_samples(args.session_id, events, config.predict)
    out = args.out or (datasets_dir(session_dir(devices_dir(data_dir), args.session_id))
                       / f"{time.strftime(RUN_FORMAT)}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "x") as file:
        for sample in samples:
            file.write(json.dumps(sample) + "\n")
    print(f"{len(samples)} samples from {len(events)} actions", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
