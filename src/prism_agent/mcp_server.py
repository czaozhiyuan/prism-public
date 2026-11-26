"""MCP stdio adapter for Agent Service v1."""

from __future__ import annotations

import json
import logging
import sys
from copy import deepcopy
from typing import Any, Callable

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from prism import __version__
from prism.agent_contract import CONTRACT
from prism.agent_service import create_service


READ_ONLY_TOOLS = (
    "prism_info", "schema_get", "project_describe_fields", "project_get",
    "module_list", "module_get", "model_list", "model_get",
    "template_list", "project_validate",
)
WRITE_TOOLS = (
    "project_create", "project_rename", "project_set_fields", "project_import_group", "project_apply", "project_recalculate",
    "project_set_model", "project_generate", "module_create", "module_update",
    "module_remove", "module_reorder", "template_configure",
)
MCP_TOOLS = READ_ONLY_TOOLS + WRITE_TOOLS


def create_server(service_factory: Callable[[], Any] = create_service) -> Server:
    """Expose Agent Service tools with the published request schemas."""
    service = service_factory()
    tools = []
    for name in MCP_TOOLS:
        schema = deepcopy(CONTRACT["tools"][name]["request_schema"])
        schema.setdefault("type", "object")
        tools.append(Tool(
            name=name,
            description="PRISM Agent Service v1 tool: {}".format(name),
            input_schema=schema,
        ))

    async def list_tools(ctx, params) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def call_tool(ctx, params) -> CallToolResult:
        if params.name not in MCP_TOOLS:
            raise MCPError(-32602, "Unknown MCP tool: {}".format(params.name))
        response = await anyio.to_thread.run_sync(
            service.call, params.name, params.arguments or {},
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(response, ensure_ascii=False))],
            structured_content=response,
            is_error=not response["ok"],
        )

    return Server(
        "PRISM", version=__version__, on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio() -> None:
    """Serve one standard MCP connection without opening a network port."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    anyio.run(serve_stdio)


if __name__ == "__main__":
    main()
