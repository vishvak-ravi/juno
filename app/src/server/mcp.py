"""The MCP server: the session tools (``server.tools.session_tools``) for other agents.

Served by the same process as the REST routes, at ``/mcp`` over streamable
HTTP, so any MCP client connects with ``http://<host>:<port>/mcp``. The
annotation tools read the same files as ``/annotations`` through
``annotate.store``, and ``predict_next_actions`` makes the prediction of
``POST /predictions`` through ``server.prediction``. Juno's other tools (past
chats, web search, bash, Claude Code) are only for its own chat model: an
agent has its own.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

MCP_PATH = "/mcp"
INSTRUCTIONS = (
    "Juno records what the user does on their computers and describes it in annotations. "
    "Each annotation covers a short stretch of activity on one device: the actions the user "
    "took and the text they were reading or writing. Call list_annotations to see recent "
    "activity on every device or on one, and get_annotation with an id from that list for "
    "the keys and clicks behind one annotation. predict_next_actions asks a model what the "
    "user is likely to do next."
)


def create_mcp(tools: list[Callable[..., Any]]) -> MCPServer:
    """The MCP server offering ``tools``, functions of ``server.tools``."""
    server = MCPServer("juno", instructions=INSTRUCTIONS)
    for tool in tools:
        server.add_tool(tool)
    return server
