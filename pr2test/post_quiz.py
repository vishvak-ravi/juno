"""Validate the agent's quiz.json, seal the key, post or refresh the PR comment.

    python -m pr2test.post_quiz --pr 12 --quiz .pr2test/quiz.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from . import gh
from .crypto import seal
from .render import is_quiz_comment, render_quiz
from .schema import AnswerKey, Quiz


def load_quiz(path: Path) -> Quiz:
    raw = path.read_text()
    try:
        return Quiz.model_validate_json(raw)
    except ValidationError as e:
        print(f"quiz.json failed validation:\n{e}", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--quiz", type=Path, default=Path(".pr2test/quiz.json"))
    ap.add_argument("--dry-run", action="store_true", help="print the comment instead of posting")
    args = ap.parse_args(argv)

    if not args.quiz.exists():
        print(f"{args.quiz} does not exist; the agent did not write a quiz", file=sys.stderr)
        return 2

    quiz = load_quiz(args.quiz)
    key = AnswerKey.from_quiz(quiz)
    body = render_quiz(quiz, args.pr, seal(key.model_dump_json()))

    if args.dry_run:
        print(body)
        return 0

    existing = [c for c in gh.list_issue_comments(args.pr) if is_quiz_comment(c["body"])]
    if existing:
        c = gh.update_comment(existing[-1]["id"], body)
        print(f"updated quiz comment {c['html_url']}")
    else:
        c = gh.create_comment(args.pr, body)
        print(f"posted quiz comment {c['html_url']}")
    print(json.dumps({"questions": len(quiz.questions)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
