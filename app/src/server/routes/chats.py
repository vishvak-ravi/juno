"""``/chats`` and ``/chats/{id}``: conversations kept as JSON files, one per chat.

A conversation is the message list the ``juno`` REPL keeps as its history and
sends to vLLM: OpenAI chat messages, ``{"role": ..., "content": ...}``, oldest
first. Chat ``<id>`` is stored in ``<chats_dir>/<id>.json``.

The model answers with the MCP server's tools (``server.mcp``) and, unless the
chat starts with a system message of its own, the system prompt in
``prompts/chat_system.txt``. Neither the prompt nor the tool calls are stored.

The list route returns a ``server.routes.paging.Page``.
"""

import logging
import os
import threading
from pathlib import Path
from typing import Annotated, Literal, Optional

from anyio import from_thread
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ValidationError

from server.llm import LlmError, complete
from server.mcp import call_chat_tool, chat_tools
from server.routes.paging import Page, page_of

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])

# The id becomes a file name, so it may not contain dots or slashes.
ChatId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]+$", max_length=128)]

# PUT reads the file, asks the model, appends and writes the file back; two at
# once must not interleave, so PUTs run one at a time.
write_lock = threading.Lock()

# Characters of the first user message kept as a chat's title in the list.
TITLE_CHARS = 80

# {max_tokens} is the configured reply limit.
SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "chat_system.txt").read_text()


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class NewMessages(BaseModel):
    messages: list[Message]


class Chat(BaseModel):
    id: str
    messages: list[Message]


class ChatSummary(BaseModel):
    id: str
    # The first line of the first user message; None if there is none.
    title: Optional[str]
    message_count: int
    # Unix seconds of the last change to the chat.
    updated_at: int


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


def summarize_chats(chats_dir: Path) -> list[ChatSummary]:
    """One summary per stored chat, in no particular order. Unreadable files are skipped."""
    summaries = []
    for path in chats_dir.glob("*.json"):
        try:
            chat = read_chat(path)
            updated_at = int(path.stat().st_mtime)
        except (OSError, ValidationError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            continue
        if chat is None:  # deleted since the glob
            continue
        summaries.append(ChatSummary(id=chat.id, title=chat_title(chat),
                                     message_count=len(chat.messages),
                                     updated_at=updated_at))
    return summaries


@router.get("")
def list_chats(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[ChatSummary]:
    """Stored chats, newest change first by default."""
    summaries = sorted(summarize_chats(request.app.state.chats_dir),
                       key=lambda s: (s.updated_at, s.id))
    return page_of(summaries, limit, order, after)


@router.get("/{chat_id}")
def get_chat(chat_id: ChatId, request: Request) -> Chat:
    chat = read_chat(chat_file(request.app.state.chats_dir, chat_id))
    if chat is None:
        raise HTTPException(status_code=404, detail=f"no chat with id {chat_id}")
    return chat


@router.put("/{chat_id}")
def append_to_chat(chat_id: ChatId, body: NewMessages, request: Request) -> Chat:
    """Append the messages and the model's reply to them, creating the chat if needed.

    The whole conversation is sent to the model, as the ``juno`` REPL does in
    multi-turn mode. If the model gives no reply, nothing is stored.
    """
    path = chat_file(request.app.state.chats_dir, chat_id)
    llm = request.app.state.llm
    mcp = request.app.state.mcp
    # This route runs in a worker thread; the MCP server's methods are coroutines.
    tools = from_thread.run(chat_tools, mcp)

    def call_tool(name: str, arguments: str) -> str:
        return from_thread.run(call_chat_tool, mcp, name, arguments)

    with write_lock:
        chat = read_chat(path) or Chat(id=chat_id, messages=[])
        chat.messages.extend(body.messages)
        messages = [message.model_dump() for message in chat.messages]
        if not messages or messages[0]["role"] != "system":
            prompt = SYSTEM_PROMPT.format(max_tokens=llm.max_tokens)
            messages.insert(0, {"role": "system", "content": prompt})
        try:
            reply = complete(llm, messages, tools, call_tool)
        except LlmError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        chat.messages.append(Message(role="assistant", content=reply))
        write_chat(path, chat)
    return chat
