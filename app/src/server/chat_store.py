"""Chats on disk, and a search over them.

A conversation is a list of OpenAI chat messages, ``{"role": ..., "content": ...}``,
oldest first. Chat ``<id>`` is stored in ``<chats_dir>/<id>.json``; the routes in
``server.routes.chats`` write the files, and the ``search_chats`` and ``get_chat``
tools of ``server.tools`` read them.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from predict.retrievers import InMemoryBM25Temporal

logger = logging.getLogger(__name__)

# The id becomes a file name, so it may not contain dots or slashes.
CHAT_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
CHAT_ID_MAX_LENGTH = 128

# Characters of the first user message kept as a chat's title.
TITLE_CHARS = 80

# Characters of a matching message a search result shows, around the first query word in it.
SNIPPET_CHARS = 400


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Chat(BaseModel):
    id: str
    messages: list[Message]


def chat_file(chats_dir: Path, chat_id: str) -> Path:
    return chats_dir / f"{chat_id}.json"


def read_chat(path: Path) -> Optional[Chat]:
    """The chat stored at ``path``, or None if there is none yet."""
    try:
        return Chat.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None


def write_chat(path: Path, chat: Chat) -> None:
    """Replace the file in one step, so a crash cannot leave half a conversation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(chat.model_dump_json(indent=2))
    os.replace(temporary, path)


def chat_title(chat: Chat) -> Optional[str]:
    for message in chat.messages:
        if message.role == "user" and message.content.strip():
            return message.content.strip().splitlines()[0][:TITLE_CHARS]
    return None


def read_chats(chats_dir: Path) -> list[tuple[Chat, int]]:
    """Every stored chat with the Unix seconds of its last change, in no particular order.
    Unreadable files are skipped."""
    chats = []
    for path in chats_dir.glob("*.json"):
        try:
            chat = read_chat(path)
            updated_at = int(path.stat().st_mtime)
        except (OSError, ValidationError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        if chat is None:  # deleted since the glob
            continue
        chats.append((chat, updated_at))
    return chats


def snippet(content: str, query: str) -> str:
    """At most ``SNIPPET_CHARS`` of ``content``, starting shortly before the first word
    of ``query`` in it; "…" marks each end that was cut."""
    words = "|".join(re.escape(word) for word in re.findall(r"\w+", query))
    found = re.search(rf"\b(?:{words})\b", content, re.IGNORECASE) if words else None
    start = max(0, (found.start() if found else 0) - SNIPPET_CHARS // 4)
    end = start + SNIPPET_CHARS
    return ("…" if start > 0 else "") + content[start:end] + ("…" if end < len(content) else "")


def search_chats(chats_dir: Path, query: str, limit: int) -> list[dict]:
    """The user and assistant messages of stored chats that match ``query`` best, best first.

    Messages are ranked with BM25 over their words. Each match is the chat's
    ``chat_id``, ``title`` and ``updated_at``, and the message's ``message``
    (its number in the chat, from 1), ``role`` and ``snippet``.
    """
    # A threshold above 1 keeps every message: the same sentence in two chats is two matches.
    index = InMemoryBM25Temporal(dedup_threshold=2.0)
    for chat, updated_at in read_chats(chats_dir):
        title = chat_title(chat)
        for number, message in enumerate(chat.messages, start=1):
            if message.role == "system":
                continue
            index.add(message.content, event_ts=updated_at,
                      metadata={"chat_id": chat.id, "title": title, "updated_at": updated_at,
                                "message": number, "role": message.role})
    # The cutoff is far ahead: a file's change time may be later than this clock's now.
    found = index.query(query, k=limit, cutoff_ts=int(time.time()) + 10 ** 9)
    return [{**match["meta"], "snippet": snippet(match["text"], query)} for match in found]
