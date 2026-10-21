"""Grade a ``/submit`` comment against the sealed key in the quiz comment.

    python -m pr2test.grade --pr 12 --comment-id 987654321
"""

from __future__ import annotations

import argparse
import sys

from . import gh
from .crypto import MissingSecret, unseal
from .render import (
    REPORT_MARKER,
    extract_key_blob,
    is_quiz_comment,
    parse_checked,
    parse_report_marker,
    parse_submit_args,
    render_report,
)
from .schema import AnswerKey, Submission, grade


def build_submission(user: str, submit_body: str, quiz_body: str, key: AnswerKey) -> Submission:
    explicit = parse_submit_args(submit_body)
    picked = explicit if explicit else parse_checked(quiz_body)
    return Submission(user=user, answers={qid: picked.get(qid) for qid in key.answers})


def previous_report(comments: list[dict], user: str) -> tuple[int | None, int]:
    """``(comment_id, attempts_so_far)`` for this user's latest report, if any."""
    mine = [
        (c["id"], attempt)
        for c in comments
        for parsed in [parse_report_marker(c["body"])]
        if parsed is not None and parsed[0] == user
        for attempt in [parsed[1]]
    ]
    if not mine:
        return None, 0
    comment_id, attempt = max(mine, key=lambda pair: pair[1])
    return comment_id, attempt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--comment-id", type=int, required=True)
    args = ap.parse_args(argv)

    submit = gh.get_comment(args.comment_id)
    user = submit["user"]["login"]
    gh.react(args.comment_id, "eyes")

    comments = gh.list_issue_comments(args.pr)
    quizzes = [c for c in comments if is_quiz_comment(c["body"])]
    if not quizzes:
        gh.create_comment(args.pr, f"{REPORT_MARKER}\n@{user} there is no quiz on this PR yet.")
        return 1
    quiz_body = quizzes[-1]["body"]

    blob = extract_key_blob(quiz_body)
    if blob is None:
        gh.create_comment(args.pr, f"{REPORT_MARKER}\n@{user} the quiz comment has no answer key; it may have been edited.")
        return 1

    try:
        key = AnswerKey.model_validate_json(unseal(blob))
    except (MissingSecret, ValueError) as e:
        gh.create_comment(args.pr, f"{REPORT_MARKER}\n@{user} could not open the answer key: {e}")
        return 1

    submission = build_submission(user, submit["body"], quiz_body, key)
    report = grade(key, submission)

    prev_id, prev_attempts = previous_report(comments, user)
    body = render_report(report, args.pr, attempt=prev_attempts + 1)
    if prev_id is None:
        c = gh.create_comment(args.pr, body)
    else:
        c = gh.update_comment(prev_id, body)
    print(f"report {c['html_url']}: {report.score}/{report.total} attempt {prev_attempts + 1}")

    sha = gh.pr_head_sha(args.pr)
    state = "success" if report.score == report.total else "failure"
    gh.set_status(sha, state, f"{user}: {report.score}/{report.total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
