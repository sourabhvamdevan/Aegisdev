"""
HTTP API routes.

Every core operation is delegated to an MCP tool via the in-process client, so
the web app genuinely uses MCP for the core workflow. Approval decisions go
through the services layer directly (they are not a tool; they are the human
gate around the tools).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..config import settings
from ..mcp_server.client import call_tool
from ..services import services

router = APIRouter()


# --- request models (typed validation) --------------------------------------
class IngestText(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class Task(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    files: Optional[dict] = None


class Decision(BaseModel):
    approval_id: str = Field(min_length=1)
    approve: bool


class SandboxRun(BaseModel):
    command: list[str] = Field(min_length=1)
    files: Optional[dict] = None
    approve_high_risk: bool = False
    approve_network: bool = False


# --- endpoints ---------------------------------------------------------------
@router.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


@router.get("/status")
async def status():
    res = await call_tool("system_status", {})
    return res.get("result", res)


@router.post("/ingest/text")
async def ingest_text(body: IngestText):
    res = await call_tool("ingest_document", {"name": body.name, "text": body.text})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res["result"]


@router.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    # Persist to the data dir then ingest by path via the MCP tool.
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.txt").name  # strip any path parts
    dest = Path(settings.data_dir) / safe_name
    dest.write_bytes(data)
    res = await call_tool("ingest_document", {"name": safe_name, "path": str(dest)})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res["result"]


@router.delete("/ingest/{source}")
async def delete_source(source: str):
    res = await call_tool("delete_content", {"source": source})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res["result"]


@router.post("/query")
async def query(body: Query):
    res = await call_tool("retrieve_context", {"query": body.query, "top_k": body.top_k})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res["result"]


@router.post("/task")
async def task(body: Task):
    import json

    args = {"task": body.task}
    if body.files:
        args["files_json"] = json.dumps(body.files)
    res = await call_tool("propose_patch", args)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res["result"]


@router.post("/approve")
async def approve(body: Decision):
    try:
        return services.decide(body.approval_id, body.approve)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/sandbox/run")
async def sandbox_run(body: SandboxRun):
    import json

    args = {
        "command": body.command,
        "approve_high_risk": body.approve_high_risk,
        "approve_network": body.approve_network,
    }
    if body.files:
        args["files_json"] = json.dumps(body.files)
    res = await call_tool("run_in_sandbox", args)
    if not res.get("ok"):
        code = 403 if res.get("needs_approval") else 400
        raise HTTPException(status_code=code, detail=res.get("error"))
    return res["result"]


@router.post("/repo/inspect")
async def repo_inspect(repo_path: str, read_file: Optional[str] = None):
    args = {"repo_path": repo_path}
    if read_file:
        args["read_file"] = read_file
    res = await call_tool("inspect_repository", args)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res["result"]
