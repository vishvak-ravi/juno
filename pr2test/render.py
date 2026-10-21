"""Markdown in, markdown out. Everything GitHub lets us draw and read back."""

from __future__ import annotations

import re

from .schema import Quiz, Report

QUIZ_MARKER = "<!-- pr2test:quiz v1 -->"
KEY_RE = re.compile(r"<!-- pr2test:key v1 (?P<blob>[A-Za-z0-9_\-=]+) -->")
REPORT_MARKER = "<!-- pr2test:report v1 -->"
_REPORT_MARKER_RE = re.compile(r"<!-- pr2test:report v1 user=(?P<user>[^ ]+) attempt=(?P<attempt>[1-9][0-9]*) -->")

_SECTION_RE = re.compile(r"^### (?P<qid>q[1-9][0-9]*)\.", re.M)
_CHECKED_RE = re.compile(r"^- \[[xX]\] \*\*(?P<key>[A-F])\.\*\*", re.M)
_SUBMIT_ARG_RE = re.compile(r"(?:q)?(?P<num>[1-9][0-9]*)\s*[:=.)]?\s*(?P<key>[A-Fa-f])\b")


def render_quiz(quiz: Quiz, pr_number: int, key_blob: str) -> str:
    out = [
        QUIZ_MARKER,
        f"## 🧪 {quiz.title}",
        "",
        "Prove you read this PR. Tick **one** box per question, then comment `/submit`.",
        "Or skip the boxes and comment `/submit 1B 2A 3C`.",
        "",
    ]
    for q in quiz.questions:
        anchor = f" <sub>`{q.anchor}`</sub>" if q.anchor else ""
        out.append(f"### {q.id}. {q.prompt}{anchor}")
        for o in q.options:
            out.append(f"- [ ] **{o.key}.** {o.text}")
        out.append("")
    out.append(f"<!-- pr2test:key v1 {key_blob} -->")
    return "\n".join(out)


def extract_key_blob(body: str) -> str | None:
    m = KEY_RE.search(body)
    return m.group("blob") if m else None


def is_quiz_comment(body: str) -> bool:
    return body.lstrip().startswith(QUIZ_MARKER)


def parse_checked(body: str) -> dict[str, str | None]:
    """Per question: the single ticked option, or None if zero or several."""
    sections = list(_SECTION_RE.finditer(body))
    answers: dict[str, str | None] = {}
    for i, sec in enumerate(sections):
        start = sec.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(body)
        checked = _CHECKED_RE.findall(body[start:end])
        answers[sec.group("qid")] = checked[0] if len(checked) == 1 else None
    return answers


def parse_submit_args(text: str) -> dict[str, str]:
    """``/submit 1B 2:a q3=C`` -> ``{"q1": "B", "q2": "A", "q3": "C"}``."""
    text = text.strip()
    if not text.lower().startswith("/submit"):
        return {}
    rest = text[len("/submit"):]
    return {f"q{m.group('num')}": m.group("key").upper() for m in _SUBMIT_ARG_RE.finditer(rest)}


def report_marker(user: str, attempt: int) -> str:
    return f"<!-- pr2test:report v1 user={user} attempt={attempt} -->"


def parse_report_marker(body: str) -> tuple[str, int] | None:
    """``(user, attempt)`` from a report comment, or None for anything else."""
    m = _REPORT_MARKER_RE.search(body)
    return (m.group("user"), int(m.group("attempt"))) if m else None


def render_report(report: Report, pr_number: int, attempt: int = 1) -> str:
    pct = round(100 * report.score / report.total) if report.total else 0
    verdict = "🟢" if pct == 100 else "🟡" if pct >= 60 else "🔴"
    nth = f" (attempt {attempt})" if attempt > 1 else ""
    out = [
        report_marker(report.user, attempt),
        f"## {verdict} pr2test report for @{report.user}: {report.score}/{report.total}{nth}",
        "",
        "| # | Yours | Correct | |",
        "|---|:-----:|:-------:|---|",
    ]
    for r in report.results:
        yours = r.selected or "—"
        mark = "✅" if r.is_correct else "❌"
        out.append(f"| {r.id} | {yours} | {r.correct} | {mark} |")
    out += ["", "<details><summary>Explanations</summary>", ""]
    for r in report.results:
        out.append(f"**{r.id}.** {r.explanation}")
        out.append("")
    out.append("</details>")
    return "\n".join(out)
