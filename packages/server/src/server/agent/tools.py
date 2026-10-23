"""The tools of Juno's chat model, as plain functions, and the registry the chat loop
calls them through.

``session_tools`` read the recorded sessions: their events, with the captions, indices
and moments of ``/events``, and screenshots. They are the only tools other agents get,
over MCP (``server.agent.mcp``).
``create_chat_tools`` adds the ones only Juno's own chat model gets: its past chats
(``server.chats``), and ``web_search``, ``bash`` and ``fwd_to_claude`` when
``ToolsConfig`` enables them. Agents that connect over MCP have tools like these of
their own.

A tool is a function: its parameters are the arguments the model may pass and its
docstring is the description the model reads. The message of a ``ToolError`` it raises
is shown to the model; what it returns is shown as is when it is text, else as JSON. A
list of text and images is shown whole over MCP; Juno's chat model is shown its text.
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
from typing import Any, Callable, Optional, Union, get_type_hints

from anyio import to_thread
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from pydantic import BaseModel, ValidationError, create_model

from annotate.request import framed_rows
from annotate.store import read_record
from juno.config import KeypointsConfig, ToolsConfig
from juno.events import EVENT_ID
from juno.sessions import (SESSION_ID_PATTERN, read_aggregations, session_dir, session_dirs,
                           session_id_of)
from keypoints.derive import Moment
from keypoints.questions import QuestionSet
from server import chats
from server.agent.claude import ClaudeError, TmuxDest, forward_to_claude
from server.agent.search import SearchError, search_web
from server.agent.shell import BashError, run_bash
from server.routes.events import Event, screenshot_file, session_events, sort_key

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
    if isinstance(result, list):
        # Text and images: the chat model is sent its tool results as text only.
        return "\n".join(item if isinstance(item, str) else "[an image you are not shown]"
                         for item in result)
    return result if isinstance(result, str) else result_text(result)


def local_time(timestamp: Optional[float]) -> Optional[str]:
    """Unix seconds as local ISO 8601, to the second."""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def result_text(result: dict) -> str:
    """A tool result as the text the model, or an MCP client, is shown."""
    return json.dumps(result, indent=2, ensure_ascii=False)


def moment_name(moment: Moment) -> str:
    """A moment's type, with its label when it has one: ``type (label)``."""
    return f"{moment.type} ({moment.label})" if moment.label else moment.type


def spans_of(events: list[Event]) -> list[list[Event]]:
    """``events`` of one session, oldest first, grouped into runs of consecutive events
    with the same caption."""
    spans: list[list[Event]] = []
    for event in events:
        if spans and spans[-1][-1].caption == event.caption:
            spans[-1].append(event)
        else:
            spans.append([event])
    return spans


def span_summary(span: list[Event], names: list[str]) -> dict:
    """One run of events as a tool shows it: its first event's id, its caption, every
    moment in it and the highest value of each of the indices ``names``."""
    first, last = span[0], span[-1]
    moments = list(dict.fromkeys(moment_name(m) for event in span for m in event.moments))
    indices = {name: round(max(event.indices[name] for event in span if name in event.indices), 2)
               for name in names if any(name in event.indices for event in span)}
    result = {"first_event": first.id, "events": len(span), "device": first.device_id,
              "start": local_time(first.timestamp), "end": local_time(last.timestamp),
              "caption": first.caption}
    if moments:
        result["moments"] = moments
    if indices:
        result["indices"] = indices
    return result


def row_of(event: Event) -> int:
    """The event's row in its session."""
    return int(EVENT_ID.match(event.id).group("row"))


def context_summary(span: list[Event]) -> dict:
    """A run of events as the context of another event."""
    return {"first_event": span[0].id, "start": local_time(span[0].timestamp),
            "caption": span[0].caption}


def screen_texts(directory: Path, chunks: set[int]) -> dict[int, str]:
    """The text on screen the annotation of each of ``chunks`` read, by chunk; chunks
    without a finished annotation are left out."""
    texts = {}
    for chunk in chunks:
        record = read_record(directory, chunk)
        if record is not None and record.annotation is not None:
            texts[chunk] = record.annotation.dense_caption
    return texts


def matches(query: str, text: str) -> bool:
    """Whether every word of ``query`` is in ``text``, ignoring case."""
    lowered = text.lower()
    return all(word in lowered for word in re.findall(r"\w+", query.lower()))


def cut_to_fit(result: dict, key: str, result_chars: int) -> bool:
    """Shorten the list ``result[key]``, from its end, until ``result`` fits in
    ``result_chars``; whether anything was cut."""
    cut = False
    while result[key] and len(result_text(result)) > result_chars:
        result[key].pop()
        cut = True
    return cut


def session_tools(devices_dir: Path, result_chars: int, chunk_size: int,
                  keypoints: KeypointsConfig, questions: QuestionSet) -> list[Callable[..., Any]]:
    """The tools over the sessions of every device stored in ``devices_dir``.

    A device's sessions are its recorder runs; a session's events are its rows, each a
    screenshot and the input after it, with the caption, indices and moments
    ``server.routes.events`` gives it.

    Args:
        result_chars: the most characters a tool's text may take; a listing returns
            fewer items than asked for rather than exceed it.
        chunk_size, keypoints, questions: how the events are read, as for ``/events``.
    """
    index_names = list(questions.indices)
    moment_types = questions.moments.state + questions.moments.action

    def known_devices(device: Optional[str]) -> list[str]:
        """Every device there is activity from; a ``ToolError`` if ``device`` is not one."""
        devices = sorted(path.name for path in devices_dir.glob("*") if path.is_dir())
        if device is not None and device not in devices:
            raise ToolError(f"no device {device}; the devices are: {', '.join(devices) or 'none'}")
        return devices

    def time_range(last_minutes: Optional[float], since: Optional[str],
                   until: Optional[str]) -> tuple[Optional[float], Optional[float]]:
        try:
            start = datetime.fromisoformat(since).timestamp() if since else None
            end = datetime.fromisoformat(until).timestamp() if until else None
        except ValueError as exc:
            raise ToolError(f"{exc}; give times as local ISO 8601, e.g. 2026-01-31T14:05:00")
        if last_minutes is not None:
            start = time.time() - last_minutes * 60
        return start, end

    def events_in(directory: Path, start: Optional[float], end: Optional[float]) -> list[Event]:
        """The events of one session between ``start`` and ``end``, oldest first."""
        events = session_events(directory, chunk_size, keypoints, questions)
        return [events[row] for row in sorted(events)
                if (start is None or events[row].timestamp >= start)
                and (end is None or events[row].timestamp <= end)]

    def list_sessions(last_minutes: Optional[float] = None, since: Optional[str] = None,
                      until: Optional[str] = None, device: Optional[str] = None,
                      limit: int = 20) -> dict:
        """The user's recording sessions, newest first: one per recorder run on a device.
        Start here to see which devices were in use when, and what stood out in each
        session: "moments" counts the moments Juno spotted in it by type (a label in
        parentheses names, e.g., which AI assistant), and "indices" is the mean of each
        index over the session, 0 to 1. Read a session's events with find_events and its
        "id". When "more_available" is true there are earlier sessions: call again with
        until set to the earliest "start" returned.

        Args:
            last_minutes: only sessions with activity from this many minutes ago until now.
            since: only sessions with activity after this local time, ISO 8601 (as in the
                "start" and "end" this tool returns). Ignored with last_minutes.
            until: only sessions with activity before this local time, ISO 8601.
            device: only this device's sessions, one of the "devices" this tool returns.
            limit: the most sessions to return.
        """
        devices = known_devices(device)
        start, end = time_range(last_minutes, since, until)
        sessions = []
        for directory in session_dirs(devices_dir, device):
            events = events_in(directory, None, None)
            if not events or (start is not None and events[-1].timestamp < start) \
                    or (end is not None and events[0].timestamp > end):
                continue
            moments: dict[str, int] = {}
            for moment in (m for event in events for m in event.moments):
                moments[moment_name(moment)] = moments.get(moment_name(moment), 0) + 1
            indices = {}
            for name in index_names:
                found = [event.indices[name] for event in events if name in event.indices]
                if found:
                    indices[name] = round(sum(found) / len(found), 2)
            sessions.append((events[-1].timestamp, {
                "id": session_id_of(directory), "device": events[0].device_id,
                "start": local_time(events[0].timestamp), "end": local_time(events[-1].timestamp),
                "events": len(events), "moments": moments, "indices": indices}))
        sessions.sort(key=lambda item: item[0], reverse=True)
        result = {"now": local_time(time.time()), "devices": devices,
                  "more_available": len(sessions) > limit,
                  "sessions": [session for _, session in sessions[:limit]]}
        if cut_to_fit(result, "sessions", result_chars):
            result["more_available"] = True
        return result

    def find_events(query: Optional[str] = None, moment: Optional[str] = None,
                    session_id: Optional[str] = None, device: Optional[str] = None,
                    last_minutes: Optional[float] = None, since: Optional[str] = None,
                    until: Optional[str] = None, limit: int = 20) -> dict:
        """What the user did, newest first, across every device unless one is named. Each
        item is a run of consecutive events of one session with the same caption: its
        "first_event" id, how many "events" it covers, its "caption", the "moments" in it
        and the highest value of each index in it, 0 to 1. With query, only runs whose
        caption or on-screen text has every word of the query, each with a "snippet" of
        that text. Read one event in full, with its screenshot, with get_event. When
        "more_available" is true there are earlier runs: call again with until set to the
        earliest "start" returned.

        Args:
            query: words to look for in what the user did or saw on screen, e.g. a
                command, a file name, a site or a person.
            moment: only runs with a moment of this type, a type (without the label in
                parentheses) from the "moments" of list_sessions.
            session_id: only this session's events, an "id" returned by list_sessions.
            device: only this device's events, one of the "devices" of list_sessions.
            last_minutes: only events from this many minutes ago until now.
            since: only events after this local time, ISO 8601 (as in the "start" and
                "end" this tool returns). Ignored with last_minutes.
            until: only events before this local time, ISO 8601.
            limit: the most runs to return.
        """
        devices = known_devices(device)
        if moment is not None and moment not in moment_types:
            raise ToolError(f"no moment type {moment}; the types are: {', '.join(moment_types)}")
        if session_id is not None:
            directory = session_dir(devices_dir, session_id) \
                if re.fullmatch(SESSION_ID_PATTERN, session_id) else None
            if directory is None or not directory.is_dir():
                raise ToolError(f"no session {session_id}; use an id from list_sessions")
            directories = [directory]
        else:
            directories = session_dirs(devices_dir, device)
        start, end = time_range(last_minutes, since, until)
        found = []
        for directory in directories:
            spans = spans_of(events_in(directory, start, end))
            if moment is not None:
                spans = [span for span in spans
                         if any(m.type == moment for event in span for m in event.moments)]
            texts = screen_texts(directory, {row_of(event) // chunk_size
                                             for span in spans for event in span}) if query else {}
            # A chunk's text on screen is shared by every run in it: a run found only by
            # text a newer run of the session already showed is left out.
            shown: set[int] = set()
            for span in reversed(spans):
                summary = span_summary(span, index_names)
                if query:
                    chunks = set(row_of(event) // chunk_size for event in span)
                    screen = "\n".join(texts[chunk] for chunk in sorted(chunks) if chunk in texts)
                    caption = span[0].caption or ""
                    if not matches(query, f"{caption}\n{screen}") \
                            or (chunks <= shown and not matches(query, caption)):
                        continue
                    if screen and not chunks <= shown:
                        summary["snippet"] = chats.snippet(screen, query)
                    shown |= chunks
                found.append((span[-1].timestamp, summary))
        found.sort(key=lambda item: item[0], reverse=True)
        result = {"now": local_time(time.time()), "devices": devices,
                  "more_available": len(found) > limit,
                  "runs": [summary for _, summary in found[:limit]]}
        if cut_to_fit(result, "runs", result_chars):
            result["more_available"] = True
        return result

    def newest_event(device: Optional[str]) -> tuple[Path, Event]:
        """The newest event of ``device``, or of any device: each device's newest session
        is the last of its sessions by id."""
        newest: dict[str, tuple[Path, Event]] = {}
        for directory in reversed(session_dirs(devices_dir, device)):
            device_id = session_id_of(directory).split("-", 1)[0]
            if device_id in newest:
                continue
            events = events_in(directory, None, None)
            if events:
                newest[device_id] = (directory, max(events, key=sort_key))
        if not newest:
            raise ToolError("nothing has been recorded" + (f" on {device}" if device else ""))
        return max(newest.values(), key=lambda found: sort_key(found[1]))

    def get_event(event_id: Optional[str] = None, device: Optional[str] = None,
                  context: int = 3) -> list[Union[str, Image]]:
        """One event in full, and its screenshot: what was on screen and what the user did
        right after. The result has its "caption", the "text_on_screen" the annotator read
        from its part of the session, its "input" (keys, clicks and scrolls), its indices
        and moments, and the captions of the runs just "before" and "after" it in its
        session. Without event_id: the newest event, e.g. for what the user is doing now.

        Args:
            event_id: a "first_event" returned by find_events, or a "first_event" of
                "before" or "after" in an earlier get_event.
            device: without event_id, the newest event of this device, one of the
                "devices" of list_sessions.
            context: how many runs before and after the event to describe.
        """
        known_devices(device)
        if event_id is None:
            directory, event = newest_event(device)
        else:
            match = EVENT_ID.match(event_id)
            directory = session_dir(devices_dir, match.group("session_id")) if match else None
            event = session_events(directory, chunk_size, keypoints, questions).get(
                int(match.group("row"))) if directory is not None else None
            if event is None:
                raise ToolError(f"no event {event_id}; use a first_event from find_events")
        row = row_of(event)
        aggregations = read_aggregations(directory)
        chunk = row // chunk_size
        record = read_record(directory, chunk)
        annotation = record.annotation if record is not None else None
        # Frame f of the annotation is row framed[f]; its input text is as the annotator read it.
        framed = framed_rows(aggregations, chunk, chunk_size)
        frame = framed.index(row) if annotation is not None and row in framed else None
        spans = spans_of(events_in(directory, None, None))
        at = next(i for i, span in enumerate(spans) if any(e.id == event.id for e in span))
        context = max(0, context)
        result = {"id": event.id, "session_id": session_id_of(directory),
                  "device": event.device_id, "time": local_time(event.timestamp),
                  "caption": event.caption,
                  "text_on_screen": annotation.dense_caption if annotation is not None else None,
                  "input": (record.frames[frame].events
                            if frame is not None and frame < len(record.frames) else None),
                  "moments": [moment_name(m) for m in event.moments],
                  "indices": {name: round(value, 2) for name, value in event.indices.items()},
                  "before": [context_summary(span) for span in spans[max(0, at - context):at]],
                  "after": [context_summary(span) for span in spans[at + 1:at + 1 + context]]}
        # The farthest runs go first, then the end of the text on screen.
        while (result["before"] or result["after"]) \
                and len(result_text(result)) > result_chars:
            if len(result["before"]) >= len(result["after"]):
                result["before"].pop(0)
            else:
                result["after"].pop()
        over = len(result_text(result)) - result_chars
        if over > 0 and result["text_on_screen"]:
            screen = result["text_on_screen"]
            # Escapes in the JSON text make it longer than the content, so cut twice the excess.
            result["text_on_screen"] = screen[:max(0, len(screen) - 2 * over)] + " [cut]"
        text = result_text(result)
        screenshot = screenshot_file(directory, aggregations[row])
        return [text, Image(path=screenshot)] if screenshot is not None else [text]

    return [list_sessions, find_events, get_event]


def create_chat_tools(devices_dir: Path, chats_dir: Path, result_chars: int,
                      tools: ToolsConfig, workspace: Path, claude_results: Path,
                      chunk_size: int, keypoints: KeypointsConfig,
                      questions: QuestionSet) -> dict[str, Tool]:
    """Every tool of Juno's chat model, by name: ``session_tools`` and the chats stored in
    ``chats_dir``, and those of ``tools`` that are enabled.

    Args:
        result_chars: the most characters a tool result may take.
            The listings, ``search_chats`` and ``get_chat`` return fewer
            items than asked for and ``bash`` cuts the middle out of its output
            rather than exceed it.
        workspace: the directory bash commands start in, and Claude Code too
            unless ``tools.claude.cwd`` names another.
        claude_results: where Claude Code reports the end of each turn.
        chunk_size, keypoints, questions: as for ``session_tools``.
    """
    functions = session_tools(devices_dir, result_chars, chunk_size, keypoints, questions)

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
        found = chats.search_chats(chats_dir, query, max(1, limit))
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
        valid = (len(chat_id) <= chats.CHAT_ID_MAX_LENGTH
                 and re.match(chats.CHAT_ID_PATTERN, chat_id))
        chat = chats.read_chat(chats.chat_file(chats_dir, chat_id)) if valid else None
        if chat is None:
            raise ToolError(f"no chat with id {chat_id}; use a chat_id from search_chats")
        numbered = [{"message": number, "role": message.role, "content": message.content}
                    for number, message in enumerate(chat.messages, start=1)
                    if number >= first_message and message.role != "system"]
        result = {"chat_id": chat.id, "title": chats.chat_title(chat),
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
