"""The MCP server: annotations, web search and bash as tools an agent can call.

Served by the same process as the REST routes, at ``/mcp`` over streamable
HTTP, so any MCP client connects with ``http://<host>:<port>/mcp``. The
annotation tools read the same files as ``/annotations`` through
``annotate.store``; ``web_search``, ``bash`` and ``fwd_to_claude`` are there when
``ToolsConfig`` enables them. Whoever can reach ``/mcp`` or ``/chats`` can have
bash commands run and prompts sent to Claude Code.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from anyio import to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from annotate.records import session_dir
from annotate.store import AnnotationRecord, list_records, parse_annotation_id, read_record
from juno.config import ToolsConfig
from server.claude import ClaudeError, TmuxDest, forward_to_claude
from server.search import SearchError, search_web
from server.shell import BashError, run_bash

MCP_PATH = "/mcp"
INSTRUCTIONS = (
    "Juno records what the user does on their computers and describes it in annotations. "
    "Each annotation covers a short stretch of activity on one device: the actions the user "
    "took and the text they were reading or writing. Call list_annotations to see recent "
    "activity on every device or on one, and get_annotation with an id from that list for "
    "the keys and clicks behind one annotation. web_search finds pages on the web and bash "
    "runs a command on the Juno server's machine, when the server has them turned on."
)


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
    """A tool result as the text an MCP client receives for it."""
    return json.dumps(result, indent=2, ensure_ascii=False)


def create_mcp(devices_dir: Path, result_chars: int, tools: ToolsConfig,
               workspace: Path, claude_results: Path) -> MCPServer:
    """The MCP server over the sessions of every device stored in ``devices_dir``.

    Args:
        result_chars: the most characters a tool result may take.
            ``list_annotations`` returns fewer annotations than ``limit`` and
            ``bash`` cuts the middle out of its output rather than exceed it.
        tools: which of ``web_search``, ``bash`` and ``fwd_to_claude`` the server
            has, and their limits.
        workspace: the directory bash commands start in, and Claude Code too
            unless ``tools.claude.cwd`` names another.
        claude_results: where Claude Code reports the end of each turn.
    """
    server = MCPServer("juno", instructions=INSTRUCTIONS)

    @server.tool()
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
        devices = sorted(path.name for path in devices_dir.glob("*") if path.is_dir())
        if device is not None and device not in devices:
            raise ToolError(f"no device {device}; the devices are: {', '.join(devices) or 'none'}")
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

    @server.tool()
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

    if tools.web_search.enabled:
        @server.tool()
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

    if tools.bash.enabled:
        @server.tool()
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

    if tools.claude.enabled:
        claude_cwd = tools.claude.cwd.expanduser() if tools.claude.cwd else workspace

        @server.tool()
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

    return server


async def chat_tools(server: MCPServer) -> list[dict]:
    """The server's tools as chat-completions tool definitions."""
    return [{"type": "function",
             "function": {"name": tool.name, "description": tool.description or "",
                          "parameters": tool.input_schema}}
            for tool in await server.list_tools()]


async def call_chat_tool(server: MCPServer, name: str, arguments: str) -> str:
    """Run one tool call a chat model made; the text to show it, errors included.

    Args:
        arguments: the call's arguments as the JSON text the model wrote.
    """
    try:
        result = await server.call_tool(name, json.loads(arguments) if arguments else {})
    except (ToolError, ValueError) as exc:
        return f"error: {exc}"
    return "\n".join(block.text for block in result.content if block.type == "text")
