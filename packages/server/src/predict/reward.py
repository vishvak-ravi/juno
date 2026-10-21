"""LongNAP's reward for one prediction: Tada's ``RewardScorer`` (``scorer.py`` at 25a28f7).

The prediction must hold an ``<actions>`` block with as many ``<action>`` tags
as the actions that followed, else it scores 0 and the judge is not asked.
Otherwise the reward is max(0, 0.5 * accuracy + 0.5 * formatting), each from
one ``grade.judge`` call; a judge reply without a score counts 0, as in Tada.
(Tada also adds a penalty, which is 0 for every prediction that gets this far.)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from grade.judge import Judge, judge_accuracy, judge_formatting

# Tada's weights (``RewardScorer`` defaults).
ACCURACY_WEIGHT = 0.5
FORMATTING_WEIGHT = 0.5


def validate(action_text: str, expected_count: int) -> list[str]:
    """Tada's ``RewardScorer.validate``: what is wrong with the prediction; empty if nothing."""
    if not action_text:
        return ["Empty or None completion."]
    if "<actions>" not in action_text or "</actions>" not in action_text:
        return ["Missing <actions> block."]
    actual_count = len(re.findall(r"<action>.*?</action>", action_text, re.DOTALL))
    if actual_count != expected_count:
        return [f"Action count mismatch: expected {expected_count}, got {actual_count}."]
    return []


def reward(prediction: str, ground_truth: str, judge: Judge) -> dict:
    """The reward for ``prediction``, with what it was based on, as ``GradedRow`` fields.

    Raises ``grade.call.CallFailed`` if a judge call fails.
    """
    result = {"reward": 0.0, "accuracy": 0.0, "formatting": 0.0,
              "validation_errors": [], "unparsed_replies": []}
    if not prediction or not re.search(r"<action>", prediction):
        result["validation_errors"] = ["No <action> tag."]
        return result
    expected_count = len(re.findall(r"<action>.*?</action>", ground_truth, re.DOTALL))
    errors = validate(prediction, expected_count)
    if errors:
        result["validation_errors"] = errors
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        accuracy_future = executor.submit(judge_accuracy, ground_truth, prediction, judge)
        formatting_future = executor.submit(judge_formatting, prediction, judge)
        accuracy, formatting = accuracy_future.result(), formatting_future.result()

    if accuracy.score is None:
        result["unparsed_replies"].append("accuracy")
    if formatting.score is None:
        result["unparsed_replies"].append("formatting")
    accuracy_value, formatting_value = accuracy.score or 0.0, formatting.score or 0.0
    result.update(
        reward=max(0.0, ACCURACY_WEIGHT * accuracy_value + FORMATTING_WEIGHT * formatting_value),
        accuracy=accuracy_value, formatting=formatting_value,
        accuracy_prompt=accuracy.prompt, accuracy_reply=accuracy.reply,
        formatting_prompt=formatting.prompt, formatting_reply=formatting.reply)
    return result
