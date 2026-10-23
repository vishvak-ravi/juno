"""The MCP server: the session tools (``server.agent.tools.session_tools``) for other agents.

Served by the same process as the REST routes, at ``/mcp`` over streamable
HTTP, so any MCP client connects with ``http://<host>:<port>/mcp``. The tools
read the stored sessions as ``/events`` does, and ``get_event`` returns the
event's screenshot as an image. Juno's other tools (past chats, web search,
bash, Claude Code) are only for its own chat model: an agent has its own.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

MCP_PATH = "/mcp"
INSTRUCTIONS = (
    "Juno is a record of what the user did on each of their devices (computers, a drawing "
    "tablet). A device's sessions are its recording runs; a session's events are "
    "screenshots, each with the input that followed it, a caption of what the user did, "
    "indices (0 to 1, e.g. how private the screen is) and moments (e.g. a prompt sent to an "
    "AI assistant, a commit, an error shown). list_sessions shows which devices were used "
    "when and what stood out; find_events finds what the user did or saw, by words on "
    "screen, moment, session or time; get_event shows one event in full with its "
    "screenshot, or the newest event of a device. Use the record to work out what the "
    "user is referring to, then look into it with your own tools."
)


def create_mcp(tools: list[Callable[..., Any]]) -> MCPServer:
    """The MCP server offering ``tools``, functions of ``server.agent.tools``."""
    server = MCPServer("juno", instructions=INSTRUCTIONS)
    for tool in tools:
        server.add_tool(tool)
    return server
