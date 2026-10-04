"""The MCP server: annotations as tools an agent can call.

Served by the same process as the REST routes, at ``/mcp`` over streamable
HTTP, so any MCP client connects with ``http://<host>:<port>/mcp``. The tools
read the same files as ``/annotations`` through ``annotate.store``.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from annotate.store import AnnotationRecord, list_records, parse_annotation_id, read_record

MCP_PATH = "/mcp"
INSTRUCTIONS = (
    "Juno records what the user does on their computer and describes it in annotations. "
    "Each annotation covers a short stretch of activity: the actions the user took and the "
    "text they were reading or writing. Call list_annotations to see recent activity, and "
    "get_annotation with an id from that list for the keys and clicks behind one annotation."
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
        "start": local_time(record.start_time),
        "end": local_time(record.end_time),
        "actions": [action.caption for action in record.annotation.actions],
        "text_on_screen": record.annotation.dense_caption,
    }


def create_mcp(sessions_dir: Path) -> MCPServer:
    """The MCP server over the sessions in ``sessions_dir``."""
    server = MCPServer("juno", instructions=INSTRUCTIONS)

    @server.tool()
    def list_annotations(last_minutes: Optional[float] = None, since: Optional[str] = None,
                         until: Optional[str] = None, limit: int = 20) -> dict:
        """What the user did on their computer, newest first.

        Args:
            last_minutes: only activity from this many minutes ago until now.
            since: only activity after this local time, ISO 8601 (as in the
                "start" and "end" this tool returns). Ignored with last_minutes.
            until: only activity before this local time, ISO 8601.
            limit: the most annotations to return.
        """
        now = time.time()
        start = datetime.fromisoformat(since).timestamp() if since else None
        if last_minutes is not None:
            start = now - last_minutes * 60
        end = datetime.fromisoformat(until).timestamp() if until else None
        done = [record for record in list_records(sessions_dir, since=start, until=end)
                if record.status == "done"]
        newest_first = sorted(done, key=lambda record: record.end_time, reverse=True)
        return {"now": local_time(now),
                "annotations": [summary(record) for record in newest_first[:limit]],
                "more_available": len(done) > limit}

    @server.tool()
    def get_annotation(annotation_id: str) -> dict:
        """One annotation in full: its actions with frame numbers, and the keys, clicks and
        scrolls recorded for each frame.

        Args:
            annotation_id: an "id" returned by list_annotations.
        """
        parsed = parse_annotation_id(annotation_id)
        record = read_record(sessions_dir / parsed[0], parsed[1]) if parsed else None
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
