"""Prompts Claude Code reports through its hook, and the moments they become.

``POST /hooks/claude-code`` (``server.routes.hooks``) appends each prompt it
keeps to ``<devices dir>/<device id>/hooks/claude_code.jsonl``, stamped with
the time the server received it. When a session's events are read, each prompt
of its device becomes an ``agent_prompt_submitted`` moment on the row whose
span holds that time, carrying the conversation so the client can resume it. On
that row it stands in for Jev's moment of the same type.
"""

import logging
import threading
from pathlib import Path
from typing import Hashable, Optional

from pydantic import BaseModel, ValidationError

from juno.sessions import Aggregation, file_stamp
from keypoints.derive import Conversation, Moment

logger = logging.getLogger(__name__)

HOOKS_DIR = "hooks"
CLAUDE_CODE_FILE = "claude_code.jsonl"
DETECTOR = "claude_code_hook"
# The ``agent_harness`` option of ``questions.json`` that names Claude Code.
HARNESS = "claude_code"
MOMENT_TYPE = "agent_prompt_submitted"

append_lock = threading.Lock()
# By prompts file: its stamp and the prompts read from it.
prompts_cache: dict[Path, tuple[Optional[tuple[int, int]], list["Prompt"]]] = {}
prompts_cache_lock = threading.Lock()


class Prompt(BaseModel):
    """One prompt submitted in a Claude Code conversation."""
    # Unix seconds, by the server's clock, when the hook's request arrived.
    timestamp: float
    session_id: str
    cwd: str
    # Claude Code's id of the prompt; None from versions that send none.
    prompt_id: Optional[str] = None


def prompts_file(device_dir: Path) -> Path:
    """Where the prompts of the device stored in ``device_dir`` are kept."""
    return device_dir / HOOKS_DIR / CLAUDE_CODE_FILE


def append_prompt(device_dir: Path, prompt: Prompt) -> None:
    path = prompts_file(device_dir)
    with append_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as lines:
            lines.write(prompt.model_dump_json() + "\n")


def read_prompts(device_dir: Path) -> list[Prompt]:
    """The device's prompts in the order they arrived; a line that is not a prompt is skipped.

    The file is read again only once its size or modification time has changed.
    Callers share the list and must not change it.
    """
    path = prompts_file(device_dir)
    stamp = file_stamp(path)
    with prompts_cache_lock:
        cached = prompts_cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        prompts = parse_prompts(path)
        prompts_cache[path] = (stamp, prompts)
        return prompts


def prompts_version(device_dir: Path, since: float, until: float) -> Hashable:
    """A value that differs whenever the prompts between ``since`` and ``until`` do."""
    return tuple(prompt.timestamp for prompt in read_prompts(device_dir)
                 if since <= prompt.timestamp <= until)


def parse_prompts(path: Path) -> list[Prompt]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    prompts = []
    for line in lines:
        if not line.strip():
            continue
        try:
            prompts.append(Prompt.model_validate_json(line))
        except ValidationError as exc:
            logger.warning("skipping a line of %s: %s", path, exc)
    return prompts


def session_end(aggregations: list[Aggregation]) -> float:
    """The last time the session's rows cover: its last row's last screenshot or input."""
    last = aggregations[-1]
    return max([last.timestamp, last.end_screenshot_timestamp or last.timestamp]
               + [event.timestamp for event in last.events])


def row_at(aggregations: list[Aggregation], at: float) -> Optional[int]:
    """The row whose span holds ``at``: from its timestamp to the next row's, the last
    row's to ``session_end``; None before the first row or after the end."""
    if not aggregations or at < aggregations[0].timestamp or at > session_end(aggregations):
        return None
    row = 0
    for index, aggregation in enumerate(aggregations):
        if aggregation.timestamp <= at:
            row = index
    return row


def prompt_moments(aggregations: list[Aggregation], prompts: list[Prompt]) -> dict[int, list[Moment]]:
    """The moments of a session's rows that ``prompts`` fall in, by row."""
    moments: dict[int, list[Moment]] = {}
    for prompt in prompts:
        row = row_at(aggregations, prompt.timestamp)
        if row is None:
            continue
        moments.setdefault(row, []).append(Moment(
            type=MOMENT_TYPE, probability=1.0, detector=DETECTOR, label=HARNESS,
            conversation=Conversation(harness=HARNESS, session_id=prompt.session_id,
                                      cwd=prompt.cwd)))
    return moments


def merge_moments(judged: dict[int, list[Moment]],
                  reported: dict[int, list[Moment]]) -> dict[int, list[Moment]]:
    """Jev's moments with the hook-reported ones; a reported type replaces Jev's on its row."""
    merged = {}
    for row in sorted(judged.keys() | reported.keys()):
        types = {moment.type for moment in reported.get(row, [])}
        merged[row] = ([m for m in judged.get(row, []) if m.type not in types]
                       + reported.get(row, []))
    return merged
