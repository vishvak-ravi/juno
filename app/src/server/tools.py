"""The tools of Juno's chat model, as plain functions, and the registry the chat loop
calls them through.

``session_tools`` read the recorded sessions: their annotations, and predictions made
from them. They are the only tools other agents get, over MCP (``server.mcp``).
``create_chat_tools`` adds the ones only Juno's own chat model gets: its past chats
(``server.chat_store``), and ``web_search``, ``bash`` and ``fwd_to_claude`` when
``ToolsConfig`` enables them. Agents that connect over MCP have tools like these of
their own.

A tool is a function: its parameters are the arguments the model may pass and its
docstring is the description the model reads. The message of a ``ToolError`` it raises
is shown to the model; what it returns is shown as is when it is text, else as JSON.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, get_type_hints

from anyio import to_thread
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError, create_model

from annotate.records import session_dir
from annotate.store import AnnotationRecord, list_records, parse_annotation_id, read_record
from juno.config import LlmConfig, ToolsConfig
from predict.predictor import Predictor
from server import chat_store
from server.claude import ClaudeError, TmuxDest, forward_to_claude
from server.llm import LlmError
from server.prediction import NotEnoughData, predict
from server.search import SearchError, search_web
from server.shell import BashError, run_bash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    # The function's docstring.
    description: str
    function: Callable[..., Any]
    # The function's parameters as a model: its JSON schema tells the model what it may
    # pass, and it checks what the model passed.
    arguments: type[BaseModel]


def tool_from_function(function: Callable[..., Any]) -> Tool:
    hints = get_type_hints(function)
    fields: dict[str, Any] = {
        name: (hints[name], ... if parameter.default is inspect.Parameter.empty
               else parameter.default)
        for name, parameter in inspect.signature(function).parameters.items()}
    return Tool(name=function.__name__, description=function.__doc__ or "",
                function=function,
                arguments=create_model(f"{function.__name__}Arguments", **fields))


def tool_definitions(tools: dict[str, Tool]) -> list[dict]:
    """The tools as chat-completions tool definitions."""
    return [{"type": "function",
             "function": {"name": tool.name, "description": tool.description,
                          "parameters": tool.arguments.model_json_schema()}}
            for tool in tools.values()]


async def call_chat_tool(tools: dict[str, Tool], name: str, arguments: str) -> str:
    """Run one tool call a chat model made; the text to show it, errors included.

    Args:
        arguments: the call's arguments as the JSON text the model wrote.
    """
    tool = tools.get(name)
    if tool is None:
        return f"error: there is no tool {name}"
    try:
        parsed = tool.arguments.model_validate_json(arguments or "{}")
        kwargs = {field: getattr(parsed, field) for field in tool.arguments.model_fields}
        if inspect.iscoroutinefunction(tool.function):
            result = await tool.function(**kwargs)
        else:
            # In a thread: the function blocks, and this runs on the server's event loop.
            result = await to_thread.run_sync(functools.partial(tool.function, **kwargs))
    except (ToolError, ValidationError) as exc:
        return f"error: {exc}"
    except Exception:
        # As over MCP, what went wrong stays in the server's log.
        logger.exception("the %s tool failed", name)
        return f"error: the {name} tool failed"
    return result if isinstance(result, str) else result_text(result)


def local_time(timestamp: Optional[float]) -> Optional[str]:
    """Unix seconds as local ISO 8601, to the second."""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def summary(record: AnnotationRecord) -> dict:
    """What an agent needs to know of a finished annotation to decide whether to read more."""
    return {
        "id": record.id,
        "device": record.device_id,
        "start": local_time(record.start_time),
        "end": local_time(record.end_time),
        "actions": [action.caption for action in record.annotation.actions],
        "text_on_screen": record.annotation.dense_caption,
    }


def result_text(result: dict) -> str:
    """A tool result as the text the model, or an MCP client, is shown."""
    return json.dumps(result, indent=2, ensure_ascii=False)


def session_tools(devices_dir: Path, result_chars: int, predictor: Predictor,
                  llm: LlmConfig) -> list[Callable[..., Any]]:
    """The tools over the sessions of every device stored in ``devices_dir``.

    Args:
        result_chars: the most characters a tool result may take; ``list_annotations``
            returns fewer annotations than asked for rather than exceed it.
        predictor: builds the request ``predict_next_actions`` sends.
        llm: the model server that request is sent to.
    """
    def known_devices(device: Optional[str]) -> list[str]:
        """Every device there is activity from; a ``ToolError`` if ``device`` is not one."""
        devices = sorted(path.name for path in devices_dir.glob("*") if path.is_dir())
        if device is not None and device not in devices:
            raise ToolError(f"no device {device}; the devices are: {', '.join(devices) or 'none'}")
        return devices

    def list_annotations(last_minutes: Optional[float] = None, since: Optional[str] = None,
                         until: Optional[str] = None, limit: int = 20,
                         device: Optional[str] = None) -> dict:
        """What the user did on their computers, newest first, each annotation naming its
        device. The result's "devices" lists every device there is activity from. When
        "more_available" is true there is earlier activity that was left out: call again
        with until set to the earliest "start" returned.

        Args:
            last_minutes: only activity from this many minutes ago until now.
            since: only activity after this local time, ISO 8601 (as in the
                "start" and "end" this tool returns). Ignored with last_minutes.
            until: only activity before this local time, ISO 8601.
            limit: the most annotations to return.
            device: only activity on this device, one of the "devices" this tool
                returns. Left out: every device, in one list ordered by time.
        """
        now = time.time()
        start = datetime.fromisoformat(since).timestamp() if since else None
        if last_minutes is not None:
            start = now - last_minutes * 60
        end = datetime.fromisoformat(until).timestamp() if until else None
        devices = known_devices(device)
        done = [record for record in list_records(devices_dir, since=start, until=end,
                                                  device_id=device)
                if record.status == "done"]
        newest_first = sorted(done, key=lambda record: record.end_time, reverse=True)
        # The oldest are dropped until the result fits, so the reader is never
        # handed a result cut off in the middle.
        result = {"now": local_time(now),
                  "devices": devices,
                  "more_available": len(done) > limit,
                  "annotations": [summary(record) for record in newest_first[:limit]]}
        while result["annotations"] and len(result_text(result)) > result_chars:
            result["annotations"].pop()
            result["more_available"] = True
        return result

    def get_annotation(annotation_id: str) -> dict:
        """One annotation in full: its actions with frame numbers, and the keys, clicks and
        scrolls recorded for each frame.

        Args:
            annotation_id: an "id" returned by list_annotations.
        """
        parsed = parse_annotation_id(annotation_id)
        record = read_record(session_dir(devices_dir, parsed[0]), parsed[1]) if parsed else None
        if record is None or record.status != "done":
            # A ToolError's message is shown to the agent; other exceptions are hidden.
            raise ToolError(f"no finished annotation with id {annotation_id}; "
                             "use an id from list_annotations")
        return {**summary(record),
                "actions": [action.model_dump() for action in record.annotation.actions],
                "frames": [{"frame": number, "time": local_time(frame.timestamp),
                            "input": frame.events}
                           for number, frame in enumerate(record.frames, start=1)]}

    async def predict_next_actions(device: Optional[str] = None) -> dict:
        """What the user is likely to do next: a model's guess at their next few actions,
        soonest first, made from their latest annotated actions, the screenshots of the
        newest ones and earlier activity that resembles them. Each call asks the model
        again, which takes a while and can give a different guess. It fails until enough
        activity has been annotated.

        Args:
            device: predict from the activity on this device only, one of the "devices"
                list_annotations returns. Left out: the activity on every device.
        """
        known_devices(device)
        try:
            # In a thread: the model call blocks, and this runs on the server's event loop.
            prediction = await to_thread.run_sync(predict, devices_dir, predictor, llm, device)
        except NotEnoughData as exc:
            raise ToolError(f"{exc}: too few of the user's actions are annotated yet")
        except OSError as exc:
            raise ToolError(f"could not read a screenshot: {exc}")
        except LlmError as exc:
            raise ToolError(str(exc))
        # The reply is an <actions> block of <action> tags; a reply without tags is kept whole.
        actions = [action.strip() for action in
                   re.findall(r"<action>(.*?)</action>", prediction.actions, re.DOTALL)]
        return {"now": local_time(time.time()),
                "predicted_actions": actions or [prediction.actions.strip()]}

    return [list_annotations, get_annotation, predict_next_actions]


def create_chat_tools(devices_dir: Path, chats_dir: Path, result_chars: int,
                      tools: ToolsConfig, workspace: Path, claude_results: Path,
                      predictor: Predictor, llm: LlmConfig) -> dict[str, Tool]:
    """Every tool of Juno's chat model, by name: ``session_tools`` and the chats stored in
    ``chats_dir``, and those of ``tools`` that are enabled.

    Args:
        result_chars: the most characters a tool result may take.
            ``list_annotations``, ``search_chats`` and ``get_chat`` return fewer
            items than asked for and ``bash`` cuts the middle out of its output
            rather than exceed it.
        workspace: the directory bash commands start in, and Claude Code too
            unless ``tools.claude.cwd`` names another.
        claude_results: where Claude Code reports the end of each turn.
        predictor, llm: as for ``session_tools``.
    """
    functions = session_tools(devices_dir, result_chars, predictor, llm)

    def search_chats(query: str, limit: int = 10) -> dict:
        """Search the user's past chats with Juno for messages about something, best match
        first. Each match names its chat ("chat_id", "title", and "updated", when the chat
        last changed), the message ("message", its number in the chat, and "role") and a
        "snippet" of the message around the words found. A message matches when it has at
        least one word of the query; a search with no match returns no "matches", so try
        other words. Read the conversation around a match with get_chat.

        Args:
            query: words the messages should contain; the order does not matter.
            limit: the most matches to return.
        """
        found = chat_store.search_chats(chats_dir, query, max(1, limit))
        result = {"matches": [{"chat_id": match["chat_id"], "title": match["title"],
                               "updated": local_time(match["updated_at"]),
                               "message": match["message"], "role": match["role"],
                               "snippet": match["snippet"]} for match in found]}
        # The worst matches are dropped until the result fits.
        while result["matches"] and len(result_text(result)) > result_chars:
            result["matches"].pop()
        return result

    def get_chat(chat_id: str, first_message: int = 1) -> dict:
        """One past chat: its messages in order, each with its number in the chat ("message"),
        "role" and "content". When "more_available" is true the chat goes on after the last
        message returned: call again with first_message set to the number after it.

        Args:
            chat_id: a "chat_id" returned by search_chats.
            first_message: the number of the first message to return.
        """
        valid = (len(chat_id) <= chat_store.CHAT_ID_MAX_LENGTH
                 and re.match(chat_store.CHAT_ID_PATTERN, chat_id))
        chat = chat_store.read_chat(chat_store.chat_file(chats_dir, chat_id)) if valid else None
        if chat is None:
            raise ToolError(f"no chat with id {chat_id}; use a chat_id from search_chats")
        numbered = [{"message": number, "role": message.role, "content": message.content}
                    for number, message in enumerate(chat.messages, start=1)
                    if number >= first_message and message.role != "system"]
        result = {"chat_id": chat.id, "title": chat_store.chat_title(chat),
                  "more_available": False, "messages": numbered}
        # The latest are dropped until the result fits; one message too long to fit is cut.
        while len(result["messages"]) > 1 and len(result_text(result)) > result_chars:
            result["messages"].pop()
            result["more_available"] = True
        over = len(result_text(result)) - result_chars
        if over > 0:
            last = result["messages"][0]
            # Escapes in the JSON text make it longer than the content, so cut twice the excess.
            last["content"] = last["content"][:max(0, len(last["content"]) - 2 * over)] + " [cut]"
        return result

    functions += [search_chats, get_chat]

    if tools.web_search.enabled:
        async def web_search(query: str, max_results: int = 5) -> dict:
            """Search the web. Returns the title, url and a short snippet of each result,
            best first. The snippets are a few sentences, not the pages: to read a page,
            fetch its url with the bash tool if there is one.

            Args:
                query: what to search for, as typed into a search engine.
                max_results: the most results to return.
            """
            count = max(1, min(max_results, tools.web_search.max_results))
            try:
                # In a thread: the search blocks, and this runs on the server's event loop.
                return {"results": await to_thread.run_sync(search_web, query, count)}
            except SearchError as exc:
                raise ToolError(str(exc))

        functions.append(web_search)

    if tools.bash.enabled:
        async def bash(command: str) -> str:
            """Run a bash command on the Juno server's machine and return what it printed
            (stdout and stderr together), followed by "[exit code N]" if it failed. Each
            call is a new shell started in the same workspace directory: files written
            there stay for later calls; variables, `cd` and background processes do not.
            A command is killed when it runs longer than the server allows, so it must not
            wait for input. Long output has its middle cut out: print less, with head,
            tail or grep.

            Args:
                command: the command line, run with `bash -c`.
            """
            try:
                return await to_thread.run_sync(run_bash, command, tools.bash, workspace,
                                                result_chars)
            except BashError as exc:
                raise ToolError(str(exc))

        functions.append(bash)

    if tools.claude.enabled:
        claude_cwd = tools.claude.cwd.expanduser() if tools.claude.cwd else workspace

        async def fwd_to_claude(prompt: str, model: Optional[str] = None,
                                tmux_dest: Optional[TmuxDest] = None) -> dict:
            """Forward a prompt to Claude Code, a stronger model with its own tools for
            files, commands and the web, and wait for its reply. Claude runs in a tmux
            pane on the Juno server's machine, where the user can watch it. It sees only
            `prompt`, nothing of this conversation. A reply can take minutes.

            The result has "reply", Claude's answer, and "tmux_dest", where it ran. The
            defaults are good: pass only `prompt` to start a conversation in a new window.

            Args:
                prompt: everything Claude needs: the task, the facts already known, and
                    what to answer with.
                model: the model of a new conversation: an alias (haiku, sonnet, opus)
                    or a full model id. Left out: the configured default.
                tmux_dest: where to run. Pass {"pane": ...} with the pane of an earlier
                    result's "tmux_dest" to continue that conversation.
            """
            try:
                return await to_thread.run_sync(
                    forward_to_claude, prompt, model, tmux_dest or TmuxDest(), tools.claude,
                    claude_results, claude_cwd, result_chars)
            except ClaudeError as exc:
                raise ToolError(str(exc))

        functions.append(fwd_to_claude)

    return {function.__name__: tool_from_function(function) for function in functions}
