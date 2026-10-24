"""The conversations the server stores: each thread an assistant's harness kept on a device.

``Conversations`` is what the lattice build, the routes and the import script
read and write through; ``FileConversations`` keeps the thread ``<session id>``
of harness ``<harness>`` on device ``<device id>`` as
``<devices dir>/<device id>/conversations/<harness>/<session id>.json``, the
``juno.conversations.Conversation`` as JSON. Saving a conversation replaces the
file, so importing a thread again brings it up to date.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Protocol

from pydantic import ValidationError

from juno.conversations import Conversation, span
from juno.sessions import DEVICE_ID_PATTERN, replace_file

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = "conversations"
# A harness or a session id as a file name: no separators.
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
DEVICE_ID = re.compile(f"^{DEVICE_ID_PATTERN}$")


class Conversations(Protocol):

    def get(self, device_id: str, harness: str, session_id: str) -> Optional[Conversation]:
        """The conversation, or None if there is none."""

    def save(self, conversation: Conversation) -> None:
        """Store the conversation, replacing the thread's earlier one."""

    def all(self, device_id: Optional[str] = None, since: Optional[float] = None,
            until: Optional[float] = None) -> list[Conversation]:
        """Stored conversations, ordered by first turn.

        ``device_id`` keeps one device's. ``since`` and ``until`` (Unix seconds)
        keep the conversations with a turn between them.
        """


def checked(value: str, pattern: re.Pattern[str], what: str) -> str:
    if not pattern.match(value):
        raise ValueError(f"not a {what}: {value!r}")
    return value


def conversation_file(devices_dir: Path, device_id: str, harness: str, session_id: str) -> Path:
    return (devices_dir / checked(device_id, DEVICE_ID, "device id") / CONVERSATIONS_DIR
            / checked(harness, NAME, "harness") / f"{checked(session_id, NAME, 'session id')}.json")


def overlaps(conversation: Conversation, since: Optional[float], until: Optional[float]) -> bool:
    start, end = span(conversation)
    return (since is None or end >= since) and (until is None or start <= until)


class FileConversations:

    def __init__(self, devices_dir: Path):
        self.devices_dir = devices_dir

    def get(self, device_id: str, harness: str, session_id: str) -> Optional[Conversation]:
        try:
            return Conversation.model_validate_json(
                conversation_file(self.devices_dir, device_id, harness, session_id).read_text())
        except FileNotFoundError:
            return None

    def save(self, conversation: Conversation) -> None:
        replace_file(conversation_file(self.devices_dir, conversation.device_id, conversation.harness,
                                       conversation.session_id),
                     conversation.model_dump_json(indent=2))

    def all(self, device_id: Optional[str] = None, since: Optional[float] = None,
            until: Optional[float] = None) -> list[Conversation]:
        conversations = []
        for path in self.devices_dir.glob(f"{device_id or '*'}/{CONVERSATIONS_DIR}/*/*.json"):
            try:
                conversation = Conversation.model_validate_json(path.read_text())
            except (OSError, ValidationError) as exc:
                logger.warning("skipping %s: %s", path, exc)
                continue
            if conversation.turns and overlaps(conversation, since, until):
                conversations.append(conversation)
        return sorted(conversations, key=lambda c: span(c)[0])
