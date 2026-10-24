"""JSON out of a model's reply, as the ``latticing`` library's ``utils`` reads it.

``parse_model_json`` cuts the JSON out of the reply (a Markdown fence or the
outermost braces), tries ``json.loads``, and on failure tries again after the
library's fixes for JSON-like text: smart quotes, comments, literal control
characters inside strings, trailing and doubled commas, missing commas between
adjacent structures. ``fallback_prompt`` is the prompt the library sends the
format model when that still fails, asking for the reply fixed to a schema.
The reply schemas the build asks the model for are here too.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, field_validator

JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
# How much of a malformed reply the format model is sent.
FALLBACK_MAX_CHARS = 12_000


class ObservationReply(BaseModel):
    """One observation as ``OBSERVE_PROMPT`` asks for it."""

    think_feel: str
    actions: str
    confidence: int
    time: Optional[str] = None


class ObservationsReply(BaseModel):
    """The whole reply to ``OBSERVE_PROMPT``."""

    observations: list[ObservationReply]


class InsightReply(BaseModel):
    """One insight as ``FORMAT_INSIGHT_PROMPT`` and ``INSIGHT_SYNTHESIS_PROMPT`` ask for it."""

    title: str
    tagline: str
    insight: str
    context: str
    supporting_evidence: list[str]
    merged: Optional[list[str]] = None

    @field_validator("supporting_evidence", mode="before")
    @classmethod
    def evidence_as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("merged", mode="before")
    @classmethod
    def merged_as_strings(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(item) for item in value]
        return value


class InsightsReply(BaseModel):
    insights: list[InsightReply]


class SupportingIdsReply(BaseModel):
    """The reply to ``MAP_EVIDENCE_PROMPT``."""

    supporting_ids: list[int]


def extract_json_snippet(text: str) -> str:
    """The most likely JSON in ``text``: a fenced block, else from the first brace to the last."""
    if not text:
        raise ValueError("No text supplied for JSON extraction.")
    match = JSON_BLOCK.search(text)
    if match:
        candidate = match.group(1)
    else:
        brace = re.search(r"[{\[]", text)
        if not brace:
            raise ValueError("No JSON object or array detected in model response.")
        opening = brace.group()
        closing = "}" if opening == "{" else "]"
        end = text.rfind(closing)
        if end == -1:
            raise ValueError("Could not find matching closing bracket for JSON payload.")
        candidate = text[brace.start():end + 1]
    candidate = candidate.strip()
    if not candidate:
        raise ValueError("Extracted JSON snippet is empty.")
    return candidate


def process_json_chars(payload: str) -> str:
    """Escape literal control characters inside strings; drop // and /* */ comments outside them."""
    result = []
    in_string = False
    escape_next = False
    i = 0
    n = len(payload)
    while i < n:
        ch = payload[i]
        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
        elif in_string:
            if ch == "\\":
                result.append(ch)
                escape_next = True
            elif ch == '"':
                in_string = False
                result.append(ch)
            elif ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            else:
                result.append(ch)
            i += 1
        elif ch == '"':
            in_string = True
            result.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and payload[i + 1] == "/":
            while i < n and payload[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < n and payload[i + 1] == "*":
            i += 2
            while i < n - 1:
                if payload[i] == "*" and payload[i + 1] == "/":
                    i += 2
                    break
                i += 1
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def sanitise_json_like(payload: str) -> str:
    """The library's fixes for text that looks like JSON but is malformed."""
    out = payload.translate(SMART_QUOTES)
    out = process_json_chars(out)
    out = re.sub(r",(\s*[}\]])", r"\1", out)
    out = re.sub(r"}\s*{", "},{", out)
    out = re.sub(r"]\s*{", "],{", out)
    out = re.sub(r"}\s*\[", "},[", out)
    out = re.sub(r"]\s*\[", "],[", out)
    out = re.sub(r",\s*,", ",", out)
    return out


def parse_model_json(text: str) -> Any:
    """The JSON in a model's reply, parsed; raises ``ValueError`` when there is none to parse."""
    snippet = extract_json_snippet(text)
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(sanitise_json_like(snippet))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unable to parse JSON from model response: {exc}") from exc


def fallback_prompt(text: str, schema: dict) -> str:
    """The prompt that asks the format model to fix a malformed reply to ``schema``."""
    try:
        snippet = extract_json_snippet(text)
    except ValueError:
        snippet = text
    snippet = snippet[:FALLBACK_MAX_CHARS]
    return ("The following is malformed JSON. Fix it so it is valid and matches the schema below. "
            "Return ONLY the corrected JSON object.\n\n"
            f"Schema:\n{json.dumps(schema, indent=2)}\n\n"
            f"Malformed JSON:\n{snippet}")
