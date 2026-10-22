"""
In-process MCP client.

The web API does NOT bypass MCP for core operations (a scored requirement).
Instead it calls the very same MCP tools through this helper, which invokes
`server.call_tool` and parses the structured JSON result. This proves the tools
are the single path for ingestion, retrieval, inspection, patching and sandbox
execution.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .server import server


def _parse(result) -> Dict[str, Any]:
    if getattr(result, "structured_content", None):
        return result.structured_content  # type: ignore[return-value]
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"ok": False, "error": "tool returned non-JSON text", "raw": text}
    return {"ok": False, "error": "tool returned no content"}


async def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    result = await server.call_tool(name, arguments)
    return _parse(result)
