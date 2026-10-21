"""Run a next-action benchmark: predict each sample of a dataset, then grade it as Tada does.

    uv run python scripts/grade_action_captions.py CONFIG.yaml

The dataset comes from ``scripts/make_prediction_dataset.py``. The grading is Tada's
LongNAP reward (``user_models/powernap/longnap/scorer.py`` at 25a28f7): the
prediction must hold an ``<actions>`` block with as many ``<action>`` tags as
the ground truth, else it scores 0; otherwise one judge call scores accuracy
against the ground truth and another scores formatting, and the reward is
max(0, 0.5 * accuracy + 0.5 * formatting) (Tada also adds a penalty, which is 0
for every prediction that gets this far). The two judge prompts below are
Tada's ``verifiers/accuracy.txt`` and ``verifiers/formatting.txt``, unmodified.

Each sample's row is written next to the session's events, in
``<session dir>/benchmarks/<run>.jsonl`` (``predict.benchmarks.GradedRow``): the
predictor and grader, what each model was sent and what it replied, and the
scores. ``GET /predictions`` lists them.

Where Tada would retry forever (online) or score 0 (offline) when a model call
fails, a call here is repeated while the provider is busy, up to
``max_attempts``; a sample whose calls still fail is recorded with its error
and left out of the means. A judge reply that is not the JSON asked for scores
0, as in Tada, and is kept in the results so it can be seen.

The config (YAML):

    dataset: path/to/dataset.jsonl
    predictor:                  # the intent model under test
      model: hosted_vllm/<served model name>    # any litellm model
      api_base: http://localhost:8000/v1        # optional
      max_tokens: 2048
      temperature: 1.0
    grader:
      model: gemini/gemini-3.1-flash-lite       # Tada's default reward_llm
    limit: null                 # grade only the first N samples
    max_attempts: 5             # calls per model request while the provider is busy
    retry_seconds: 60           # wait between them when the provider names none

Keys come from the environment or ``.env`` (GEMINI_API_KEY for gemini/).
Where sessions are stored comes from ``capture.data_dir`` in ``config.yaml``
(or JUNO_CONFIG_PATH).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional, TextIO
from urllib.parse import unquote, urlparse

import litellm
import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from annotate.label import PROVIDER_BUSY, retry_after_seconds
from juno.sessions import session_dir
from juno.config import ConfigError, Section, devices_dir, load_config, resolve_data_dir
from predict.benchmarks import (RUN_FORMAT, GradedRow, GraderSettings, PredictorSettings,
                                run_file)
from predict.predictor import image_part

ROOT_DIR = Path(__file__).resolve().parents[1]

# Tada's weights (``RewardScorer`` defaults).
ACCURACY_WEIGHT = 0.5
FORMATTING_WEIGHT = 0.5


class BenchmarkConfig(Section):
    dataset: Path
    predictor: PredictorSettings
    grader: GraderSettings = GraderSettings()
    limit: Optional[int] = None
    max_attempts: int = 5
    retry_seconds: float = 60.0


class CallFailed(Exception):
    """A model call failed, or kept failing while the provider was busy."""


def call(model: str, api_base: Optional[str], messages: list[dict], config: BenchmarkConfig,
         **params) -> str:
    """The reply text of one litellm call, repeated while the provider is busy."""
    for attempt in range(1, config.max_attempts + 1):
        try:
            response = litellm.completion(model=model, messages=messages, api_base=api_base,
                                          **params)
            return response.choices[0].message.content or ""
        except PROVIDER_BUSY as exc:
            if attempt == config.max_attempts:
                raise CallFailed(f"{model}: {type(exc).__name__}: {exc}") from exc
            wait = retry_after_seconds(exc) or config.retry_seconds
            print(f"  {model} busy ({type(exc).__name__}); retrying in {wait:.0f}s",
                  file=sys.stderr)
            time.sleep(wait)
        except Exception as exc:  # whatever litellm raises: bad key, bad request, bad model
            raise CallFailed(f"{model}: {type(exc).__name__}: {exc}") from exc
    raise AssertionError("unreachable")


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


def json_reply(text: str) -> Optional[dict]:
    """The JSON object in a judge reply, from a code block if there is one; None if none."""
    text = (text or "").strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        text = match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def accuracy_score(reply: str) -> Optional[float]:
    parsed = json_reply(reply)
    try:
        return float(parsed["candidates"][0]["score"])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def formatting_score(reply: str) -> Optional[float]:
    parsed = json_reply(reply)
    try:
        return float(parsed["score"])
    except (TypeError, KeyError, ValueError):
        return None


def grade(prediction: str, ground_truth: str, judge: Callable[[str], str]) -> dict:
    """Tada's ``RewardScorer._call_judge_sync`` for one prediction, with what it was based on.

    Returns ``GradedRow`` fields. Raises ``CallFailed`` if a judge call fails.
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

    accuracy_prompt = ACCURACY_PROMPT.format(
        ground_truth=ground_truth, candidates=f"- **Candidate 1**:\n{prediction}\n")
    formatting_prompt = FORMATTING_PROMPT.format(prediction=prediction)
    with ThreadPoolExecutor(max_workers=2) as executor:
        accuracy_future = executor.submit(judge, accuracy_prompt)
        formatting_future = executor.submit(judge, formatting_prompt)
        accuracy_reply, formatting_reply = accuracy_future.result(), formatting_future.result()

    accuracy, formatting = accuracy_score(accuracy_reply), formatting_score(formatting_reply)
    if accuracy is None:
        result["unparsed_replies"].append("accuracy")
    if formatting is None:
        result["unparsed_replies"].append("formatting")
    accuracy, formatting = accuracy or 0.0, formatting or 0.0
    result.update(
        reward=max(0.0, ACCURACY_WEIGHT * accuracy + FORMATTING_WEIGHT * formatting),
        accuracy=accuracy, formatting=formatting,
        accuracy_prompt=accuracy_prompt, accuracy_reply=accuracy_reply,
        formatting_prompt=formatting_prompt, formatting_reply=formatting_reply)
    return result


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

    def judge(prompt: str) -> str:
        return call(config.grader.model, config.grader.api_base,
                    [{"role": "user", "content": prompt}], config)

    # One file per session the samples come from.
    files: dict[str, TextIO] = {}
    rows: list[GradedRow] = []
    try:
        for index, sample in enumerate(samples):
            fields = {}
            try:
                fields["prediction"] = call(
                    config.predictor.model, config.predictor.api_base,
                    with_images(sample["messages"]), config,
                    max_tokens=config.predictor.max_tokens,
                    temperature=config.predictor.temperature)
                fields.update(grade(fields["prediction"], sample["ground_truth"], judge))
            except (CallFailed, OSError) as exc:
                fields = {"prediction": fields.get("prediction"), "error": str(exc)}
            row = GradedRow(
                session_id=sample["session_id"], run=run, index=index, graded_at=time.time(),
                dataset=str(config.dataset.resolve()), predictor=config.predictor,
                grader=config.grader, cutoff_ts=sample.get("cutoff_ts"),
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
        "predictor": config.predictor.model, "grader": config.grader.model,
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


# Tada's verifiers/accuracy.txt, unmodified.
ACCURACY_PROMPT = """You are an expert evaluator.

Your task is to compare a **ground truth sequence of actions** with several **candidate predicted sequences of actions**, and assign a **quality score** to each candidate based purely on **accuracy**.

Each action is an event describing what the user did on a computer.

Your goal is to evaluate **how accurately each candidate reproduces the ground truth trajectory** in terms of correctness, order, and semantic fidelity.

---

# Evaluation Dimensions

Evaluate candidates holistically using the following dimensions.

### 1. Action Correctness
- Do predicted actions correspond to real actions in the ground truth?
- Are there hallucinated actions not present in the ground truth?
- Are important ground truth actions missing?

Minor extra or missing actions may be acceptable if the overall trajectory is preserved.

---

### 2. Order & Temporal Consistency
- Are actions in the correct order relative to the ground truth?
- Small local reordering is acceptable if it does not change task meaning.
- Major reordering that changes the task flow should be heavily penalized.

---

### 3. Semantic Fidelity
- Even if wording differs, does the candidate describe the same intent as the ground truth?
- Clear paraphrases count as matches.
- Actions describing different user intent should be penalized.
- If an action in the candidate maps to a different action in the ground truth, this is incorrect.

---

# Scoring Guidelines

Assign a **single score between 0.0 and 1.0** to each candidate.

Use **continuous values** (up to two decimal places), and distribute scores meaningfully across candidates.

### Score Calibration

- **0.90 – 1.00**  
  Near-perfect or perfect match.  
  All actions are present, correct, and in the right order. Minor wording differences are acceptable.

- **0.75 – 0.89**  
  Largely correct trajectory.  
  Most actions match in intent and order, with minor missing details or small ordering issues.

- **0.50 – 0.74**  
  Partial match.  
  Key actions are correct, but multiple missing steps or incorrect ordering.

- **0.25 – 0.49**  
  Weak match.  
  Minimal overlap with the ground truth; many hallucinated or incorrect actions.

- **0.00 – 0.24**  
  No meaningful overlap.  
  The candidate does not resemble the ground truth trajectory.

---

# Important Rules

- Always **rank candidates relative to each other**.
- Do NOT assign identical scores unless candidates are truly equivalent.
- If one candidate is even slightly better than another, their scores must reflect that difference.
- Avoid over-penalizing minor flaws: assess the **overall severity** of errors rather than subtracting for every issue.
- Hallucinated actions and missing core steps should lower the score proportionally to how much they distort the trajectory.

---

# Examples

## Perfect Accuracy (Score ≈ 0.95–1.00)

Ground Truth:
<action>Switched to the terminal application on workspace 4.</action>
<action>Ran command `cd programming/code_puzzles` in the terminal.</action>
<action>Activated python environment using conda command `conda activate cpuzzles`.</action>
<action>Ran the command `time py -m fib_number 100000` in the terminal.</action>
<action>Interrupted the running script using `CTRL + C`.</action>
<action>Switched from the terminal to the Google Chrome browser on workspace 3.</action>
<action>Entered `Fibonacci number dynamic programming` into Google search.</action>
<action>Clicked on the link titled `GeeksforGeeks Fibonacci Sequence`.</action>

Candidate:
<action>Switched to the terminal application on workspace 4.</action>
<action>Ran command `cd programming/code_puzzles` in the terminal.</action>
<action>Activated python environment using conda command `conda activate cpuzzles`.</action>
<action>Ran the command `time py -m fib_number 100000` in the terminal.</action>
<action>Interrupted the running script using `CTRL + C`.</action>
<action>Switched from the terminal to the Google Chrome browser on workspace 3.</action>
<action>Entered `Fibonacci number dynamic programming` into Google search.</action>
<action>Clicked on the link titled `GeeksforGeeks Fibonacci Sequence`.</action>

Explanation:
- All actions are present and in correct order.
- Each action correctly maps to a ground truth action.
- Perfect semantic match with identical or near-identical wording.
- No hallucinated or missing steps.

---

## Good Accuracy (Score ≈ 0.80–0.90)

Ground Truth:
<action>Switched to the Google Chrome browser on workspace 2.</action>
<action>Clicked on the `Gmail` bookmark in the Chrome bookmarks bar.</action>
<action>Scrolled through emails in the Gmail inbox.</action>
<action>Clicked the `Delete` button on the email titled `We've updated our Terms of Service` from sender `Figma`.</action>
<action>Clicked on the Gmail label `university`.</action>
<action>Scrolled through emails under the `university` label.</action>
<action>Opened the email titled `Updated certificate of study`.</action>
<action>Clicked the download button for the attached file `study_certificate.pdf`.</action>

Candidate:
<action>Switched to the Google Chrome browser on workspace 2.</action>
<action>Typed `gmail.com` into the address bar and pressed Enter.</action>
<action>Scrolled through emails in the Gmail inbox.</action>
<action>Clicked the `Delete` button on the email titled `We've updated our Terms of Service` from sender `Figma`.</action>
<action>Clicked on the Gmail label `university`.</action>
<action>Scrolled through emails under the `university` label.</action>
<action>Opened the email titled `Updated certificate of study`.</action>
<action>Clicked the download button for the attached file `study_certificate.pdf`.</action>

Explanation:
- 7 out of 8 actions are correctly identified and ordered.
- Action 2 is semantically equivalent (navigating to Gmail) but uses a different method (typing URL vs clicking bookmark).
- All other actions match exactly.
- Very high accuracy with only one minor method difference.

---

## Partial Accuracy (Score ≈ 0.55–0.70)

Ground Truth:
<action>Opened the Files application.</action>
<action>Navigated to the directory `~/Documents/Thesis`.</action>
<action>Opened the file `ProjectProposal.docx` in LibreOffice Writer.</action>
<action>Edited the introduction paragraph and inserted a citation.</action>
<action>Saved the document as `ProjectProposal_v2.docx`.</action>
<action>Opened Thunderbird email client.</action>
<action>Composed an email to `professor@example.com` with the updated proposal attached.</action>
<action>Clicked the `Send` button.</action>

Candidate:
<action>Opened the Files application.</action>
<action>Opened the file `ProjectProposal.docx` in LibreOffice Writer.</action>
<action>Edited the introduction paragraph and inserted a citation.</action>
<action>Saved the document using `Ctrl+S`.</action>
<action>Opened Thunderbird email client.</action>
<action>Composed an email to `professor@example.com` with the updated proposal attached.</action>
<action>Clicked the `Send` button.</action>

Explanation:
- Missing navigation to directory `~/Documents/Thesis` (action 2 in ground truth).
- Action 4 shows regular save (`Ctrl+S`) instead of "Save As" with new filename `ProjectProposal_v2.docx`.
- 5 out of 8 ground truth actions are correctly captured.
- Overall trajectory is recognizable but with missing steps and one incorrect action.

---

## Weak Accuracy (Score ≈ 0.25–0.45)

Ground Truth:
<action>Opened the Files application.</action>
<action>Navigated to the directory `~/Documents/Thesis`.</action>
<action>Opened the file `ProjectProposal.docx` in LibreOffice Writer.</action>
<action>Edited the introduction paragraph and inserted a citation.</action>
<action>Saved the document as `ProjectProposal_v2.docx`.</action>
<action>Opened Thunderbird email client.</action>
<action>Composed an email to `professor@example.com` with the updated proposal attached.</action>
<action>Clicked the `Send` button.</action>

Candidate:
<action>Opened the terminal application.</action>
<action>Ran command `git pull origin main` in the terminal.</action>
<action>Opened Visual Studio Code from the application launcher.</action>
<action>Opened the file `main.py` in Visual Studio Code.</action>
<action>Edited the `calculate_metrics()` function.</action>
<action>Saved the file `main.py` using `Ctrl+S`.</action>
<action>Opened Thunderbird email client.</action>
<action>Clicked the `Send` button on a draft email.</action>

Explanation:
- Most actions are incorrect: terminal, git command, VS Code, and Python file instead of Files, navigation, LibreOffice, and Word document.
- Only 2 out of 8 ground truth actions match: "Opened Thunderbird email client" and partially "Clicked the `Send` button".
- Missing email composition action entirely.
- Wrong application context for most of the sequence.

---

## Complete Mismatch (Score ≈ 0.00–0.15)

Ground Truth:
<action>Opened the Files application.</action>
<action>Renamed `budget_2025.xlsx` to `budget_2025_final.xlsx`.</action>
<action>Uploaded the file to Google Drive.</action>

Candidate:
<action>Opened Spotify application from the dock.</action>
<action>Searched for the playlist `Chill Vibes 2026`.</action>
<action>Clicked the play button on the first track.</action>

Explanation:
- No shared applications, intent, or actions.
- All actions are specific but describe entirely unrelated user behavior.
- Completely different task domain (music playback vs file management).

---

# Input Format

## Ground Truth
{ground_truth}

## Candidates
{candidates}

---

# Output Format

Respond with JSON object:

```json
{{
  "candidates": [
    {{ "id": 1, "score": 0.85 }},
    {{ "id": 2, "score": 0.72 }}
  ]
}}
```"""

# Tada's verifiers/formatting.txt, unmodified.
FORMATTING_PROMPT = """You are an expert evaluator.

Your task is to evaluate a **a sequence of actions** based purely on **formatting quality**.

Each action is an event describing what the user did on a computer.

---

# Evaluation Dimensions

Evaluate the prediction using the following two dimensions.

### 1. Specificity
- Are actions concrete and specific, or vague and generic?
- Do actions include identifying details (filenames, link titles, query text, UI labels, numeric values)?
- Generic actions receive severe penalties.

Examples:
- **Specific**: "Clicked on article titled `Fed Signals Interest Rate Cuts Could Begin in 2026`."
- **Generic**: "Clicked on a post." ❌

- **Specific**: "Downloaded the file `study_certificate.pdf`."
- **Generic**: "Downloaded a file." ❌

- **Specific**: "Ran command `time py -m fib_number 100000` in the terminal."
- **Generic**: "Ran a Python command." ❌

- **Specific**: "Switched to the Google Chrome browser on workspace 3."
- **Slightly generic**: "Switched to the Chrome browser." (missing workspace)
- **Very generic**: "Switched to a browser." ❌

---

### 2. Action Granularity (No Packing)
- Each action should describe a single, atomic user operation.
- Packing multiple distinct actions into a single line is a MAJOR formatting flaw.
- Actions should not be compressed, merged, or combined.

Examples of problematic packing:
- "Opened Gmail and deleted spam emails." ❌ (Should be: "Opened Gmail." + "Deleted spam emails.")
- "Navigated to Documents and opened project file." ❌ (Should be: "Navigated to Documents folder." + "Opened project file.")
- "Searched for documentation and clicked first result." ❌ (Should be: "Searched for documentation." + "Clicked first result.")
- "Composed and sent email to professor." ❌ (Should be: "Composed email to professor." + "Clicked Send button.")

---

### 3. Hedging & Ambiguity
- Actions should be clear and definitive, not hedging or listing multiple possibilities.
- Phrases like "clicked or scrolled" or "might have opened" are formatting flaws.

Examples:
- **Clear**: "Scrolled down the page."
- **Hedging**: "Scrolled or clicked to move down the page." ❌

- **Clear**: "Opened the Settings menu."
- **Hedging**: "Opened Settings or Preferences." ❌

---

# Scoring Guidelines

Assign a **single score between 0.0 and 1.0** to the prediction.

Use **continuous values** (up to two decimal places).

### Score Calibration

- **0.90 – 1.00**  
  Excellent formatting.  
  All actions are highly specific with concrete details. Each action describes a single operation. No hedging or ambiguity.

- **0.75 – 0.89**  
  Good formatting.  
  Most actions are specific and granular. Minor genericness in 1-2 actions or slight compression acceptable.

- **0.50 – 0.74**  
  Moderate formatting.  
  Multiple generic actions OR several instances of action packing. Some specificity preserved but significant room for improvement.

- **0.25 – 0.49**  
  Poor formatting.  
  Pervasive genericness (e.g., "clicked a link", "opened a file") and/or heavy action packing. Most actions lack necessary detail.

- **0.00 – 0.24**  
  Very poor formatting.  
  Nearly all actions are generic or heavily compressed. Prediction provides minimal useful detail.

---

# Important Rules

- **Penalize genericness severely**: Every instance of "a file", "a link", "a browser", "an email" without specifics should lower the score.
- **Penalize action packing severely**: Each instance of multiple actions compressed into one should significantly lower the score.
- **Reward specificity**: Filenames, link titles, exact commands, UI element labels, search queries should increase the score.
- **Be strict**: These are formatting standards. High scores should be reserved for predictions that maintain both specificity and granularity throughout.

---

# Examples

## Excellent Formatting (Score ≈ 0.95–1.00)

Prediction:
<action>Switched to the terminal application on workspace 4.</action>
<action>Ran command `cd programming/code_puzzles` in the terminal.</action>
<action>Activated python environment using conda command `conda activate cpuzzles`.</action>
<action>Ran the command `time py -m fib_number 100000` in the terminal.</action>
<action>Interrupted the running script using `CTRL + C`.</action>
<action>Switched from the terminal to the Google Chrome browser on workspace 3.</action>
<action>Entered `Fibonacci number dynamic programming` into Google search.</action>
<action>Clicked on the link titled `GeeksforGeeks Fibonacci Sequence`.</action>

Explanation:
- Every action is highly specific with concrete details.
- Commands, query text, and link titles are preserved.
- Each action is atomic and properly separated.
- No packing, no hedging, no genericness.

---

## Good Formatting (Score ≈ 0.80–0.88)

Prediction:
<action>Opened the Files application.</action>
<action>Navigated to the directory `~/Documents/Thesis`.</action>
<action>Opened the file `ProjectProposal.docx` in LibreOffice Writer.</action>
<action>Edited the introduction paragraph.</action>
<action>Inserted a citation.</action>
<action>Saved the document as `ProjectProposal_v2.docx`.</action>
<action>Opened Thunderbird email client.</action>
<action>Composed an email to `professor@example.com`.</action>
<action>Attached the updated proposal.</action>
<action>Clicked the `Send` button.</action>

Explanation:
- Most actions are specific with filenames, paths, and email addresses.
- Actions are properly granular and separated.
- Minor issue: "Edited the introduction paragraph" is slightly generic (doesn't specify what edit), but acceptable.
- Overall excellent formatting with concrete details.

---

## Moderate Formatting (Score ≈ 0.50–0.65)

Prediction:
<action>Switched to a web browser.</action>
<action>Opened Gmail.</action>
<action>Looked through inbox emails.</action>
<action>Deleted a Terms of Service email from Figma.</action>
<action>Opened university-related emails.</action>
<action>Opened an email about a study certificate.</action>
<action>Downloaded a certificate PDF.</action>

Explanation:
- Multiple generic actions: "a web browser" (not "Chrome"), "Looked through" (vague), "a certificate PDF" (no filename).
- Some action packing: "Opened university-related emails" suggests multiple actions or a navigation step that's not granular.
- "a Terms of Service email" loses the exact title.
- Partial specificity (Gmail, Figma mentioned) but overall too generic.

---

## Poor Formatting (Score ≈ 0.30–0.45)

Prediction:
<action>Opened terminal and navigated to project directory.</action>
<action>Ran some Python commands.</action>
<action>Switched to browser and searched for documentation.</action>
<action>Clicked a link and read the page.</action>
<action>Went back to terminal.</action>
<action>Edited a file and saved it.</action>

Explanation:
- Severe action packing: nearly every action combines 2+ operations.
- Pervasive genericness: "some Python commands", "a link", "the page", "a file".
- No concrete identifiers (filenames, search queries, link titles).
- Formatting quality is very poor despite possibly describing correct actions.

---

## Very Poor Formatting (Score ≈ 0.10–0.20)

Prediction:
<action>Did some work in the terminal.</action>
<action>Opened a browser and looked at websites.</action>
<action>Checked email and responded to messages.</action>
<action>Downloaded files and organized them.</action>

Explanation:
- Extreme genericness: no concrete details whatsoever.
- Extreme action packing: each action represents multiple distinct operations.
- Completely fails both formatting criteria.
- Provides almost no useful information about what actually happened.

---

# Input Format

## Prediction
{prediction}

---

# Output Format

Respond with JSON object:

```json
{{
  "score": 0.72
}}
```
"""


if __name__ == "__main__":
    sys.exit(main())
