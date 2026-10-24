"""Claude Code's transcripts read as conversations.

Claude Code keeps one JSON Lines file per session under
``<config dir>/projects/<working directory as a slug>/<session id>.jsonl``
(``CLAUDE_CONFIG_DIR``, default ``~/.claude``). A line is a record with a
``type``; the ``user`` and ``assistant`` records carry the ``message`` with its
``content`` (a string, or blocks of ``text``, ``thinking``, ``tool_use``,
``tool_result``, ``image``), an ISO ``timestamp``, the ``cwd``, the
``entrypoint`` Claude Code ran with and ``isSidechain`` on a subagent's records.

``read_transcript`` keeps what the user and Claude said, by the same rule the
hook's route keeps a prompt (``server.routes.hooks.unsupported``): records of a
Claude Code the device can reopen (``KEPT_ENTRYPOINTS``), none of a subagent's.
A user's turn is a record with a prompt id whose text is not one of Claude
Code's own notes (a command's record or output, an interruption, an image
placeholder; ``NOTE_PREFIXES``); a tool result has no text and is dropped with
them. An assistant's turn is the text blocks of its records; consecutive
assistant records, split by its tool calls, make one turn. The session id is
the file's name, what ``claude --resume`` takes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from juno.claude_hook import KEPT_ENTRYPOINTS
from juno.conversations import CLAUDE_CODE, Conversation, Turn

logger = logging.getLogger(__name__)

CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
PROJECTS_DIR = "projects"
# A user record whose text starts with one of these is Claude Code's own note, not the user's words.
NOTE_PREFIXES = ("<", "[Request interrupted", "[Image:")


def default_projects_dir() -> Path:
    """Where Claude Code keeps this user's transcripts."""
    return Path(os.environ.get(CONFIG_DIR_ENV) or "~/.claude").expanduser() / PROJECTS_DIR


def text_of(content: Any) -> str:
    """The text of a message's content: the string itself, or its text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content
                         if isinstance(block, dict) and block.get("type") == "text"
                         and isinstance(block.get("text"), str))
    return ""


def record_time(value: Any) -> Optional[float]:
    """Unix seconds from the record's ISO timestamp; None when it has none."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def read_transcript(path: Path, device_id: str) -> Optional[Conversation]:
    """The conversation in one transcript file; None when it holds no turn of the user's."""
    turns: list[Turn] = []
    cwd: Optional[str] = None
    entrypoint: Optional[str] = None
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") not in ("user", "assistant"):
            continue
        if record.get("isSidechain") or record.get("isMeta"):
            continue
        if record.get("entrypoint") not in KEPT_ENTRYPOINTS:
            continue
        message = record.get("message")
        text = text_of(message.get("content") if isinstance(message, dict) else None).strip()
        if not text:
            continue
        role = record["type"]
        if role == "user" and (not record.get("promptId") or text.startswith(NOTE_PREFIXES)):
            continue
        at = record_time(record.get("timestamp"))
        if at is None:
            continue
        if cwd is None:
            cwd = str(record.get("cwd") or "")
            entrypoint = record["entrypoint"]
        if role == "assistant" and turns and turns[-1].role == "assistant":
            turns[-1].text = f"{turns[-1].text}\n{text}"
            continue
        turns.append(Turn(role=role, text=text, time=at,
                          id=record.get("promptId") if role == "user" else None))
    if not any(turn.role == "user" for turn in turns):
        return None
    turns.sort(key=lambda turn: turn.time)
    return Conversation(harness=CLAUDE_CODE, session_id=path.stem, device_id=device_id, cwd=cwd or "",
                        entrypoint=entrypoint or "", turns=turns)


def read_conversations(projects_dir: Path, device_id: str) -> list[Conversation]:
    """Every conversation under ``projects_dir``, ordered by first turn; a file that cannot be read is skipped."""
    conversations = []
    for path in sorted(projects_dir.glob("*/*.jsonl")):
        try:
            conversation = read_transcript(path, device_id)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        if conversation is not None:
            conversations.append(conversation)
    return sorted(conversations, key=lambda c: c.turns[0].time)
