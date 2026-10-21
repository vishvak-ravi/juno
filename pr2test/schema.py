"""Typed contract between the quiz-writing agent and the deterministic grader.

The agent emits a ``Quiz``. The poster strips it into a public rendering plus an
encrypted ``AnswerKey``. The grader turns a ``Submission`` into a ``Report``.

Only ``mcq`` exists today. ``Question`` is the seam: when a second type lands,
make it ``Annotated[Union[MCQ, Other], Field(discriminator="type")]`` and add a
grader for it in ``grade()``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

OptionKey = Annotated[str, StringConstraints(pattern=r"^[A-F]$")]
QuestionId = Annotated[str, StringConstraints(pattern=r"^q[1-9][0-9]*$")]


class Option(BaseModel):
    key: OptionKey
    text: str = Field(min_length=1, max_length=400)


class MCQ(BaseModel):
    type: Literal["mcq"] = "mcq"
    id: QuestionId
    prompt: str = Field(min_length=10, max_length=1500)
    options: list[Option] = Field(min_length=2, max_length=6)
    answer: OptionKey
    explanation: str = Field(
        min_length=1,
        max_length=1500,
        description="Shown in the report after grading. Never shown before.",
    )
    anchor: str | None = Field(
        default=None,
        description="Optional `path/to/file.py:123` the question is about.",
    )

    @model_validator(mode="after")
    def _keys_in_order_and_answer_present(self) -> "MCQ":
        keys = [o.key for o in self.options]
        expected = [chr(ord("A") + i) for i in range(len(keys))]
        if keys != expected:
            raise ValueError(f"option keys must be {expected} in order, got {keys}")
        if self.answer not in keys:
            raise ValueError(f"answer {self.answer!r} is not one of {keys}")
        return self


Question = MCQ


class Quiz(BaseModel):
    version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=200)
    questions: list[Question] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _ids_sequential(self) -> "Quiz":
        ids = [q.id for q in self.questions]
        expected = [f"q{i}" for i in range(1, len(ids) + 1)]
        if ids != expected:
            raise ValueError(f"question ids must be {expected}, got {ids}")
        return self

    def public(self) -> "Quiz":
        """Copy with answers and explanations blanked, safe to show the taker."""
        return self.model_copy(
            update={
                "questions": [
                    q.model_copy(update={"answer": "A", "explanation": "-"})
                    for q in self.questions
                ]
            }
        )


class AnswerKey(BaseModel):
    version: Literal[1] = 1
    answers: dict[QuestionId, OptionKey]
    explanations: dict[QuestionId, str]

    @classmethod
    def from_quiz(cls, quiz: Quiz) -> "AnswerKey":
        return cls(
            answers={q.id: q.answer for q in quiz.questions},
            explanations={q.id: q.explanation for q in quiz.questions},
        )


class Submission(BaseModel):
    user: str
    answers: dict[QuestionId, OptionKey | None] = Field(
        description="None means nothing, or more than one thing, was selected."
    )


class QuestionResult(BaseModel):
    id: QuestionId
    selected: OptionKey | None
    correct: OptionKey
    is_correct: bool
    explanation: str


class Report(BaseModel):
    version: Literal[1] = 1
    user: str
    score: int
    total: int
    results: list[QuestionResult]


def grade(key: AnswerKey, submission: Submission) -> Report:
    """Deterministic MCQ grading: exact match on the option key."""
    results: list[QuestionResult] = []
    for qid, correct in key.answers.items():
        selected = submission.answers.get(qid)
        results.append(
            QuestionResult(
                id=qid,
                selected=selected,
                correct=correct,
                is_correct=selected == correct,
                explanation=key.explanations[qid],
            )
        )
    return Report(
        user=submission.user,
        score=sum(r.is_correct for r in results),
        total=len(results),
        results=results,
    )
