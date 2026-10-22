"""
MCP integration tests.

Proves an MCP client can DISCOVER tools/resources/prompts and INVOKE them
(a mandatory requirement). Uses the in-process server + client.
"""

import pytest

from app.mcp_server.client import call_tool
from app.mcp_server.server import server


@pytest.mark.asyncio
async def test_discovery_lists_required_capabilities():
    tools = await server.list_tools()
    names = {t.name for t in tools}
    required = {
        "ingest_document",
        "retrieve_context",
        "inspect_repository",
        "propose_patch",
        "run_in_sandbox",
    }
    assert required.issubset(names)
    assert len(names) >= 6  # exceeds the mandatory 5 (targets the bonus tier)

    prompts = await server.list_prompts()
    assert any(p.name == "code_review" for p in prompts)

    resources = await server.list_resources()
    assert any("aegisdev://status" in str(r.uri) for r in resources)


@pytest.mark.asyncio
async def test_invoke_ingest_and_retrieve_via_mcp():
    r1 = await call_tool("ingest_document", {"name": "mcp.md", "text": "network is denied by default"})
    assert r1["ok"] is True
    r2 = await call_tool("retrieve_context", {"query": "network default", "top_k": 2})
    assert r2["ok"] is True
    assert "citations" in r2["result"]


@pytest.mark.asyncio
async def test_sandbox_tool_blocks_unsafe_command():
    r = await call_tool("run_in_sandbox", {"command": ["bash", "-c", "echo hi"]})
    assert r["ok"] is False
    assert "allowlist" in r["error"]


@pytest.mark.asyncio
async def test_tool_actionable_error_on_empty_query():
    r = await call_tool("retrieve_context", {"query": "   "})
    assert r["ok"] is False
    assert "empty" in r["error"].lower()


@pytest.mark.asyncio
async def test_sandbox_tool_accepts_json_object_files_json():
    import json

    # Regression: files_json is a JSON *string*. Some MCP SDK versions
    # aggressively pre-parse any string argument that looks like a JSON
    # object/array before validating it against the tool's declared type,
    # which corrupted this exact field (a dict slipped through where a str
    # was expected) and made every sandbox run with seed files 500.
    r = await call_tool(
        "run_in_sandbox",
        {
            "command": ["python", "solution.py"],
            "files_json": json.dumps({"solution.py": "print(2 + 2)"}),
        },
    )
    assert r["ok"] is True
    assert r["result"]["exit_code"] == 0
    assert r["result"]["stdout"].strip() == "4"


@pytest.mark.asyncio
async def test_delete_content_tool_removes_indexed_chunks():
    await call_tool("ingest_document", {"name": "to_delete.md", "text": "ephemeral content"})
    r = await call_tool("delete_content", {"source": "to_delete.md"})
    assert r["ok"] is True
    assert r["result"]["removed"] >= 1
    r2 = await call_tool("retrieve_context", {"query": "ephemeral content", "top_k": 5})
    sources = {c["source"] for c in r2["result"]["citations"]}
    assert "to_delete.md" not in sources


@pytest.mark.asyncio
async def test_propose_patch_accepts_json_object_files_json():
    import json

    r = await call_tool(
        "propose_patch",
        {
            "task": "add a solve function",
            "files_json": json.dumps({"solve.py": "def solve(x):\n    return x\n"}),
        },
    )
    assert r["ok"] is True
    assert r["result"]["success"] is True
