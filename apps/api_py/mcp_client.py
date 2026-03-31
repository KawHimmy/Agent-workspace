from __future__ import annotations

import os
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .config import settings
from .store import (
    sync_tool_registry,
    upsert_mcp_server,
    upsert_mcp_server_connection,
)

MCP_SERVER_NAME = "workspace-mcp-server"
MCP_SERVER_SCRIPT = "packages/mcp-servers/src/document-server.js"


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command="node",
        args=[MCP_SERVER_SCRIPT],
        cwd=str(settings.root_dir),
        env={
            **os.environ,
            "APP_STORE_FILE": str(settings.store_file),
        },
    )


def mcp_result_to_json(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    return {"value": str(result)}


def extract_mcp_text(result: Any) -> str:
    payload = mcp_result_to_json(result)
    content = payload.get("content")
    if not isinstance(content, list):
        return str(payload)

    texts = [
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and item.get("text")
    ]
    return "\n".join(texts).strip()


async def call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(name, arguments)


async def list_mcp_tools(user_id: str | None = None) -> list[dict[str, Any]]:
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = [tool.model_dump(mode="json") for tool in result.tools]

    await upsert_mcp_server(
        MCP_SERVER_NAME,
        transport="stdio",
        status="connected",
        metadata={"script": MCP_SERVER_SCRIPT},
    )
    await upsert_mcp_server_connection(
        MCP_SERVER_NAME,
        user_id=user_id,
        status="connected",
        metadata={"script": MCP_SERVER_SCRIPT},
    )
    await sync_tool_registry(MCP_SERVER_NAME, tools)
    return tools


async def test_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await call_mcp_tool(name, arguments)
    return {
        "name": name,
        "arguments": arguments,
        "result": mcp_result_to_json(result),
        "text": extract_mcp_text(result),
    }
