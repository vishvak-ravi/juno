"""Removes secrets from an ``AnnotationRequest`` before it leaves the machine.

``sanitize`` is the one place this happens: it returns a copy of the request
with its ``sanitization`` set, and ``annotate.label.annotate`` refuses a request
without one. The ``Sanitization`` names the algorithm and counts what it
replaced, and is stored next to the model's output, so the output of two
algorithms can be compared later.

This first algorithm reads only the typed keys in each frame's event text. It
does not look at the screenshots, and it does not see a secret typed across two
frames or broken up by a correction (backspace, arrow keys).
"""

from __future__ import annotations

import re
from typing import Optional, TypeVar

from pydantic import BaseModel

# AnnotationRequest; annotate.request imports this module for ``Sanitization``.
AnnotationRequestT = TypeVar("AnnotationRequestT", bound=BaseModel)

ALGORITHM = "typed-key-regex"
VERSION = 1

# napsack writes typed keys as "Key pressed: a", "Keys pressed: a|b" or "Key(s) pressed: a|b".
KEY_LINE = re.compile(r"^(?P<head>\s*Key(?:s|\(s\))? pressed: )(?P<keys>.*)$")
# Keys held while typing a character; they sit between the characters of a word.
HELD_KEYS = frozenset({"shift", "shift_l", "shift_r", "caps_lock"})

# Checked in this order; an earlier pattern keeps the characters it matched.
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("api_key", re.compile(
        r"sk-[A-Za-z0-9_-]{16,}"            # OpenAI, Anthropic
        r"|AIza[A-Za-z0-9_-]{35}"           # Google
        r"|gh[pousr]_[A-Za-z0-9]{36,}"      # GitHub
        r"|github_pat_[A-Za-z0-9_]{22,}"
        r"|AKIA[A-Z0-9]{16}"                # AWS access key id
        r"|xox[abprs]-[A-Za-z0-9-]{10,}"    # Slack
        r"|hf_[A-Za-z0-9]{30,}")),          # Hugging Face
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("card_number", re.compile(r"(?<!\d)\d{13,19}(?!\d)")),
    # Anything long with both letters and digits and no spaces: a token or hash.
    ("long_token", re.compile(r"(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{32,}")),
)


class Sanitization(BaseModel):
    """Which algorithm cleaned a request, and what it replaced."""

    algorithm: str
    version: int
    # Whether the event text and the screenshots were looked at.
    text: bool
    images: bool
    # Pattern name -> how many matches were replaced, zero included.
    redactions: dict[str, int]


def placeholder(name: str) -> str:
    return f"[REDACTED:{name}]"


def passes_luhn(digits: str) -> bool:
    """Whether ``digits`` has a valid payment-card checksum."""
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit)
        if position % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def split_keys(keys: str) -> list[str]:
    """The key names in a ``|``-joined list; the ``|`` key itself shows up as two empty parts."""
    parts = keys.split("|")
    tokens = []
    index = 0
    while index < len(parts):
        if parts[index] == "" and index + 1 < len(parts) and parts[index + 1] == "":
            tokens.append("|")
            index += 2
        else:
            tokens.append(parts[index])
            index += 1
    return tokens


def find_secrets(typed: str) -> list[tuple[int, int, str]]:
    """Non-overlapping ``(start, end, pattern name)`` spans of ``typed`` that match a pattern."""
    spans: list[tuple[int, int, str]] = []
    for name, pattern in PATTERNS:
        for match in pattern.finditer(typed):
            if name == "card_number" and not passes_luhn(match.group()):
                continue
            if all(match.end() <= start or match.start() >= end for start, end, _ in spans):
                spans.append((match.start(), match.end(), name))
    return sorted(spans)


def sanitize_keys(tokens: list[str], counts: dict[str, int]) -> list[str]:
    """``tokens`` with each typed secret replaced by one placeholder; adds to ``counts``."""
    # Runs of single-character keys, as (characters typed, index of each in tokens).
    runs: list[tuple[str, list[int]]] = []
    typed, positions = "", []
    for index, token in enumerate(tokens):
        if len(token) == 1:
            typed += token
            positions.append(index)
        elif token not in HELD_KEYS:
            runs.append((typed, positions))
            typed, positions = "", []
    runs.append((typed, positions))

    replaced: dict[int, Optional[str]] = {}  # token index -> placeholder, or None to drop
    for typed, positions in runs:
        for start, end, name in find_secrets(typed):
            counts[name] += 1
            first, last = positions[start], positions[end - 1]
            replaced.update({index: None for index in range(first, last + 1)})
            replaced[first] = placeholder(name)
    return [replaced.get(index, token) for index, token in enumerate(tokens)
            if replaced.get(index, token) is not None]


def sanitize_events(events: str, counts: dict[str, int]) -> str:
    """One frame's event text with typed secrets replaced; adds to ``counts``."""
    lines = []
    for line in events.split("\n"):
        match = KEY_LINE.match(line)
        if match:
            keys = sanitize_keys(split_keys(match.group("keys")), counts)
            line = match.group("head") + "|".join(keys)
        lines.append(line)
    return "\n".join(lines)


def sanitize(request: AnnotationRequestT) -> AnnotationRequestT:
    """A copy of ``request`` (an ``AnnotationRequest``) that may be sent out.

    The copy's ``sanitization`` says what was done to it.
    """
    counts = {name: 0 for name, _ in PATTERNS}
    frames = [frame.model_copy(update={"events": sanitize_events(frame.events, counts)})
              for frame in request.frames]
    return request.model_copy(update={
        "frames": frames,
        "sanitization": Sanitization(algorithm=ALGORITHM, version=VERSION, text=True,
                                     images=False, redactions=counts),
    })
