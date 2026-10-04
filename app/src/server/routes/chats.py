"""``/chats`` and ``/chats/{id}``: conversations kept as JSON files, one per chat.

A conversation is the message list the ``juno`` REPL keeps as its history and
sends to vLLM: OpenAI chat messages, ``{"role": ..., "content": ...}``, oldest
first. Chat ``<id>`` is stored in ``<chats_dir>/<id>.json`` (``server.chat_store``).

The model answers with the MCP server's tools (``server.mcp``) and, unless the
chat starts with a system message of its own, the system prompt in
``prompts/chat_system.txt``. Neither the prompt nor the tool calls are stored.

The list route returns a ``server.routes.paging.Page``.
"""

import threading
from pathlib import Path
from typing import Annotated, Literal, Optional

from anyio import from_thread
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from server.chat_store import (CHAT_ID_MAX_LENGTH, CHAT_ID_PATTERN, Chat, Message, chat_file,
                               chat_title, read_chat, read_chats, write_chat)
from server.llm import LlmError, complete
from server.mcp import call_chat_tool, chat_tools
from server.routes.paging import Page, page_of

router = APIRouter(prefix="/chats", tags=["chats"])

ChatId = Annotated[str, PathParam(pattern=CHAT_ID_PATTERN, max_length=CHAT_ID_MAX_LENGTH)]

# PUT reads the file, asks the model, appends and writes the file back; two at
# once must not interleave, so PUTs run one at a time.
write_lock = threading.Lock()

# {max_tokens} is the configured reply limit.
SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "chat_system.txt").read_text()


class NewMessages(BaseModel):
    messages: list[Message]


class ChatSummary(BaseModel):
    id: str
    # The first line of the first user message; None if there is none.
    title: Optional[str]
    message_count: int
    # Unix seconds of the last change to the chat.
    updated_at: int


def summarize_chats(chats_dir: Path) -> list[ChatSummary]:
    """One summary per stored chat, in no particular order. Unreadable files are skipped."""
    return [ChatSummary(id=chat.id, title=chat_title(chat), message_count=len(chat.messages),
                        updated_at=updated_at)
            for chat, updated_at in read_chats(chats_dir)]


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
