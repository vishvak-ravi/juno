"""Tada's judge, alone and as LongNAP's reward."""

import json

from grade.judge import judge_accuracy, judge_formatting
from predict.predictor import build_actions_block
from predict.reward import reward


def block(*captions):
    return build_actions_block([{"text": caption} for caption in captions])


class FakeJudge:
    """Replies with a fixed score; records every prompt it is sent."""

    def __init__(self, accuracy=0.8, formatting=0.6, reply=None):
        self.prompts = []
        self.accuracy, self.formatting, self.reply = accuracy, formatting, reply

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.reply is not None:
            return self.reply
        if "## Candidates" in prompt:
            return "```json\n" + json.dumps({"candidates": [{"id": 1, "score": self.accuracy}]}) + "\n```"
        return json.dumps({"score": self.formatting})


def test_captions_are_judged_against_a_reference_whatever_their_number():
    judge = FakeJudge(accuracy=0.7)
    reference = block("Opened the file `notes.txt`.", "Typed a heading.", "Saved the file.")
    candidate = block("Opened `notes.txt` in the editor.", "Saved it with Ctrl+S.")

    judgement = judge_accuracy(reference, candidate, judge)

    assert judgement.score == 0.7
    assert reference in judge.prompts[0] and candidate in judge.prompts[0]


def test_formatting_is_judged_without_a_reference():
    judge = FakeJudge(formatting=0.9)
    candidate = block("Clicked the `Send` button.")

    judgement = judge_formatting(candidate, judge)

    assert judgement.score == 0.9 and candidate in judgement.prompt


def test_a_reply_that_is_not_the_json_asked_for_has_no_score():
    judgement = judge_accuracy(block("A."), block("B."), FakeJudge(reply="I think 0.5"))
    assert judgement.score is None and judgement.reply == "I think 0.5"


def test_the_reward_weighs_accuracy_and_formatting_equally():
    result = reward(block("A.", "B."), block("A.", "C."), FakeJudge(accuracy=0.8, formatting=0.6))
    assert result["reward"] == 0.5 * 0.8 + 0.5 * 0.6
    assert result["validation_errors"] == []


def test_a_prediction_with_the_wrong_number_of_actions_scores_zero_unjudged():
    judge = FakeJudge()
    result = reward(block("A."), block("A.", "B."), judge)
    assert result["reward"] == 0.0 and result["validation_errors"]
    assert judge.prompts == []
