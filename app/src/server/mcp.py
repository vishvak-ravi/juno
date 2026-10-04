"""The MCP server: annotations as tools an agent can call.

Served by the same process as the REST routes, at ``/mcp`` over streamable
HTTP, so any MCP client connects with ``http://<host>:<port>/mcp``. The tools
read the same files as ``/annotations`` through ``annotate.store``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from annotate.records import session_dir
from annotate.store import AnnotationRecord, list_records, parse_annotation_id, read_record

MCP_PATH = "/mcp"
INSTRUCTIONS = (
    "Juno records what the user does on their computers and describes it in annotations. "
    "Each annotation covers a short stretch of activity on one device: the actions the user "
    "took and the text they were reading or writing. Call list_annotations to see recent "
    "activity on every device or on one, and get_annotation with an id from that list for "
    "the keys and clicks behind one annotation."
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


def create_mcp(devices_dir: Path, result_chars: int) -> MCPServer:
    """The MCP server over the sessions of every device stored in ``devices_dir``.

    Args:
        result_chars: the most characters a ``list_annotations`` result may take;
            it returns fewer annotations than ``limit`` rather than exceed it.
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
