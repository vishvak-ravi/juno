"""Grade one annotator's action captions against another's, chunk by chunk, with Tada's judge.

    uv run python scripts/grade_action_captions.py REFERENCE CANDIDATE
        [--chunk N] [--limit N] [--formatting] [--out FILE]

REFERENCE and CANDIDATE are annotation stores of the same session: a directory
holding ``annotations/NNNN.json`` (``annotate.store``), either a session in the
server's store or a run of ``scripts/annotate_session_activity.py`` under
``data/annotations/``. A chunk is graded when both have a finished annotation
of it over the same frames. Its two ``<actions>`` blocks, one ``<action>`` per
caption in the annotator's order, go to ``grade.judge``: the accuracy judge
scores the candidate against the reference, and with ``--formatting`` the
formatting judge scores each side alone. Nothing here asks for a number of
actions; each annotator writes as many as it saw.

Rows are appended to ``--out``, by default
``data/grades/<session id>/<candidate model>--vs--<reference model>.jsonl``
(``/`` in a model as ``_``), one per chunk graded: both blocks, each judge's
prompt, reply and score, and the grader. A chunk that already has every
judgement asked for is skipped and a missing judgement is added, so a run
stopped by the provider's daily limit continues where it left off.
``--limit`` caps the chunks graded in one run; each costs one judge call, three
with ``--formatting``.

The judge is ``grade.model`` in ``config.yaml``; keys come from the environment
or ``.env`` (GEMINI_API_KEY for gemini/).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import litellm
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from annotate.store import ANNOTATIONS_DIR, AnnotationRecord, read_record
from grade.call import CallFailed, judge_for
from grade.judge import Judgement, judge_accuracy, judge_formatting
from juno.config import ConfigError, load_config
from predict.predictor import build_actions_block

ROOT_DIR = Path(__file__).resolve().parents[1]
GEMINI_KEY_ENV = "GEMINI_API_KEY"


class Side(BaseModel):
    """One annotation store being compared."""
    path: str
    model: str


class CaptionGrade(BaseModel):
    """One line of the output: a chunk's two caption sequences and the judgements on them."""
    session_id: str
    chunk: int
    graded_at: float
    grader: str
    reference: Side
    candidate: Side
    reference_actions: str
    candidate_actions: str
    # How closely the candidate reproduces the reference, 0 to 1.
    accuracy: Optional[Judgement] = None
    # Each side's formatting alone, 0 to 1; None unless asked for.
    candidate_formatting: Optional[Judgement] = None
    reference_formatting: Optional[Judgement] = None
    # Set if a judge call failed; the judgements made before it are kept.
    error: Optional[str] = None


def slug(model: str) -> str:
    return model.replace("/", "_")


def stored_chunks(store: Path) -> list[int]:
    return sorted(int(path.stem) for path in (store / ANNOTATIONS_DIR).glob("*.json")
                  if path.stem.isdigit())


def actions_block(record: AnnotationRecord) -> str:
    return build_actions_block([{"text": action.caption}
                                for action in record.annotation.actions])


def comparable(reference: Optional[AnnotationRecord],
               candidate: Optional[AnnotationRecord]) -> bool:
    """Whether both records are finished annotations of the same frames."""
    return (reference is not None and candidate is not None
            and reference.status == "done" and candidate.status == "done"
            and [f.screenshot_path for f in reference.frames]
            == [f.screenshot_path for f in candidate.frames])


def read_grades(path: Path) -> dict[int, CaptionGrade]:
    """The latest row for each chunk in ``path``; empty if there is no file."""
    latest: dict[int, CaptionGrade] = {}
    if not path.exists():
        return latest
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = CaptionGrade.model_validate_json(line)
        except ValidationError:
            continue
        latest[row.chunk] = row
    return latest


def missing(row: Optional[CaptionGrade], formatting: bool) -> list[str]:
    """The judgements ``row`` still lacks."""
    wanted = ["accuracy"] + (["candidate_formatting", "reference_formatting"] if formatting else [])
    return [name for name in wanted if row is None or getattr(row, name) is None]


def mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reference", type=Path, help="the reference annotation store")
    parser.add_argument("candidate", type=Path, help="the candidate annotation store")
    parser.add_argument("--chunk", type=int, help="grade only this chunk")
    parser.add_argument("--limit", type=int, help="grade at most this many chunks in this run")
    parser.add_argument("--formatting", action="store_true",
                        help="also score each side's formatting (two more judge calls a chunk)")
    parser.add_argument("--out", type=Path, help="the JSONL file rows are appended to")
    args = parser.parse_args(argv)

    load_dotenv(ROOT_DIR / ".env")
    try:
        config = load_config(None if os.environ.get("JUNO_CONFIG_PATH")
                             else ROOT_DIR / "config.yaml").grade
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if config.model.startswith("gemini/") and not os.environ.get(GEMINI_KEY_ENV):
        print(f"error: {GEMINI_KEY_ENV} is not set. Put {GEMINI_KEY_ENV}=<your key> in "
              f"{ROOT_DIR / '.env'}, or export it.", file=sys.stderr)
        return 2

    chunks = [args.chunk] if args.chunk is not None else sorted(
        set(stored_chunks(args.reference)) & set(stored_chunks(args.candidate)))
    pairs = [(chunk, read_record(args.reference, chunk), read_record(args.candidate, chunk))
             for chunk in chunks]
    pairs = [(chunk, ref, cand) for chunk, ref, cand in pairs if comparable(ref, cand)]
    if not pairs:
        print("error: the two stores have no finished annotation of the same frames",
              file=sys.stderr)
        return 2
    session_ids = {ref.session_id for _, ref, _ in pairs} | {cand.session_id for _, _, cand in pairs}
    if len(session_ids) != 1:
        print(f"error: the stores hold different sessions: {sorted(session_ids)}",
              file=sys.stderr)
        return 2
    session_id = session_ids.pop()
    reference = Side(path=str(args.reference.resolve()), model=pairs[0][1].model)
    candidate = Side(path=str(args.candidate.resolve()), model=pairs[0][2].model)
    out = args.out or (ROOT_DIR / "data" / "grades" / session_id
                       / f"{slug(candidate.model)}--vs--{slug(reference.model)}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)

    judge = judge_for(config)
    existing = read_grades(out)
    todo = [(chunk, ref, cand) for chunk, ref, cand in pairs
            if missing(existing.get(chunk), args.formatting)][:args.limit]
    print(f"{session_id}: {len(pairs)} comparable chunks, {len(todo)} to grade "
          f"({candidate.model} vs {reference.model}, judge {config.model}) -> {out}",
          file=sys.stderr)

    calls = 0
    for number, (chunk, ref, cand) in enumerate(todo, start=1):
        row = existing.get(chunk) or CaptionGrade(
            session_id=session_id, chunk=chunk, graded_at=time.time(), grader=config.model,
            reference=reference, candidate=candidate,
            reference_actions=actions_block(ref), candidate_actions=actions_block(cand))
        update: dict = {"graded_at": time.time(), "error": None}
        stop = False
        try:
            for name in missing(row, args.formatting):
                if name == "accuracy":
                    update[name] = judge_accuracy(row.reference_actions, row.candidate_actions,
                                                  judge)
                elif name == "candidate_formatting":
                    update[name] = judge_formatting(row.candidate_actions, judge)
                else:
                    update[name] = judge_formatting(row.reference_actions, judge)
                calls += 1
        except CallFailed as exc:
            update["error"] = str(exc)
            # The daily limit does not lift by retrying the next chunk.
            stop = isinstance(exc.__cause__, litellm.RateLimitError)
        row = row.model_copy(update=update)
        with open(out, "a") as file:
            file.write(row.model_dump_json() + "\n")
        accuracy = row.accuracy.score if row.accuracy else None
        print(f"[{number}/{len(todo)}] chunk {chunk}: "
              f"{row.error or f'accuracy {accuracy}'}", file=sys.stderr)
        if stop:
            print("stopping: the provider's rate limit was reached", file=sys.stderr)
            break

    rows = [row for row in read_grades(out).values() if row.error is None]

    def scores(name: str) -> list[float]:
        return [getattr(row, name).score for row in rows
                if getattr(row, name) is not None and getattr(row, name).score is not None]

    print(json.dumps({
        "session_id": session_id, "reference": reference.model, "candidate": candidate.model,
        "grader": config.model, "judge_calls": calls, "graded_chunks": len(rows),
        "accuracy": mean(scores("accuracy")),
        "candidate_formatting": mean(scores("candidate_formatting")),
        "reference_formatting": mean(scores("reference_formatting")),
        "unparsed_replies": sum(1 for row in rows for name in
                                ("accuracy", "candidate_formatting", "reference_formatting")
                                if getattr(row, name) is not None
                                and getattr(row, name).score is None),
        "out": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
