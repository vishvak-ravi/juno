"""A conversation with an assistant: one thread of a harness on a device, as the server stores it.

Every harness keeps its threads its own way (Claude Code as JSON Lines under its
config directory, a chat app as an export); a reader per harness in
``capture.chats`` turns each thread into one ``Conversation``, the shape the
server stores (``server.conversations``) and the lattice reads
(``lattice.build``): where the thread ran, and its ``turns``, each the user's or
the assistant's text at a time.
"""

from typing import Literal, Optional

from pydantic import BaseModel

# ``Conversation.harness`` of Claude Code: the ``agent_harness`` option of ``questions.json`` that names it.
CLAUDE_CODE = "claude_code"
# Harness -> the name the assistant is given when its turns are written out.
ASSISTANT_NAMES = {CLAUDE_CODE: "Claude Code"}

Role = Literal["user", "assistant"]


class Turn(BaseModel):
    """What one side said once."""

    role: Role
    text: str
    # Unix seconds, by the clock of the device the conversation ran on.
    time: float
    # The harness's own id of the turn, when it gives one; Claude Code's prompt id on a user's turn.
    id: Optional[str] = None


class Conversation(BaseModel):
    harness: str
    # The harness's id of the thread, e.g. what ``claude --resume`` takes.
    session_id: str
    # The device the conversation ran on, as its sessions are stored on the server.
    device_id: str
    # The directory the conversation ran in, on that device.
    cwd: str
    # Where the harness ran, as Claude Code's CLAUDE_CODE_ENTRYPOINT names it: ``cli`` in a
    # terminal, ``claude-desktop`` in the Claude desktop app.
    entrypoint: str
    # The Claude desktop app's own id of the session, what its ``claude://code/continue?session=``
    # link takes; None when the harness's own files do not record it.
    host_session_id: Optional[str] = None
    # In time order.
    turns: list[Turn]


def assistant_name(harness: str) -> str:
    """The name the assistant of ``harness`` is given in a transcript."""
    return ASSISTANT_NAMES.get(harness, harness)


def span(conversation: Conversation) -> tuple[float, float]:
    """Unix seconds of the first and last turn."""
    times = [turn.time for turn in conversation.turns]
    return min(times), max(times)
