"""
Shared services layer.

Single source of truth for the RAG pipeline, coding agent, repository
inspection, and the pending-approval registry. Both the MCP tools and the HTTP
API build on this, so behaviour is identical whichever entry point is used.

Repository inspection reads a repo directory safely (jailed to the repo root,
size-capped) without executing anything.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .agent.loop import AgentReport, CodingAgent
from .config import settings
from .logging_config import audit_event
from .rag.pipeline import RagPipeline

_TEXT_EXTS = {".py", ".js", ".ts", ".md", ".txt", ".json", ".cfg", ".ini", ".toml"}


def _select_patch_generator():
    """Choose the agent's patch generator from config (defaults to builtin)."""
    if settings.patch_generator == "ollama":
        from .agent.ollama_generator import make_ollama_generator

        return make_ollama_generator(
            host=settings.ollama_host,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
        )
    # builtin: deterministic, offline, zero-cost (also used by CI and the demo).
    from .agent.loop import _default_patch_generator

    return _default_patch_generator


class Services:
    def __init__(self):
        self.rag = RagPipeline(db_path=settings.db_path, retrieval_mode=settings.retrieval_mode)
        self.agent = CodingAgent(self.rag, patch_generator=_select_patch_generator())
        # pending patches awaiting human approval: id -> (report, files)
        self._pending: Dict[str, dict] = {}

    # -- provider transparency ---------------------------------------------
    def status(self) -> dict:
        return {
            "app": settings.app_name,
            "provider_mode": settings.provider_mode,
            "embedding_backend": self.rag.backend,
            "patch_generator": settings.patch_generator,
            "data_processing": "local" if settings.provider_mode == "local" else "external",
            "index": self.rag.stats(),
            "pending_approvals": list(self._pending.keys()),
        }

    # -- ingestion / retrieval ---------------------------------------------
    def ingest_text(self, name: str, content: str) -> dict:
        result = self.rag.ingest_text(name, content)
        audit_event("ingest", source=name, chunks=result.get("chunks"))
        return result

    def ingest_path(self, path: Path) -> dict:
        result = self.rag.ingest_file(path)
        audit_event("ingest", source=path.name, chunks=result.get("chunks"))
        return result

    def delete_source(self, source: str) -> int:
        removed = self.rag.delete(source)
        audit_event("delete", source=source, removed=removed)
        return removed

    def retrieve(self, query: str, top_k: int = 5) -> dict:
        r = self.rag.retrieve(query, top_k=top_k)
        if r.flagged_chunks:
            audit_event("injection_flagged", query=query[:80], count=r.flagged_chunks)
        return {
            "query": r.query,
            "context": r.context,
            "citations": [c.__dict__ for c in r.citations],
            "retrieval_mode": r.mode,
            "flagged_chunks": r.flagged_chunks,
        }

    # -- repository inspection ---------------------------------------------
    def inspect_repository(self, repo_path: str, max_files: int = 200) -> dict:
        root = Path(repo_path).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("repo_path is not a directory")
        files: List[dict] = []
        for p in sorted(root.rglob("*")):
            if len(files) >= max_files:
                break
            if p.is_dir() or any(part.startswith(".") for part in p.relative_to(root).parts):
                continue
            if p.suffix.lower() not in _TEXT_EXTS:
                continue
            rel = str(p.relative_to(root))
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append({"path": rel, "bytes": size, "ext": p.suffix})
        return {"root": str(root), "file_count": len(files), "files": files}

    def read_repo_file(self, repo_path: str, rel_path: str, max_bytes: int = 200_000) -> str:
        root = Path(repo_path).resolve(strict=True)
        target = (root / rel_path).resolve()
        # jail: the resolved target must stay under root (blocks ../ + symlinks)
        if root != target and root not in target.parents:
            raise ValueError("path escapes repository root")
        data = target.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    # -- coding task (agent) -----------------------------------------------
    def run_task(self, task: str, files: Optional[Dict[str, str]] = None) -> dict:
        report: AgentReport = self.agent.run(task, files)
        approval_id = uuid.uuid4().hex[:12]
        self._pending[approval_id] = {
            "created": time.time(),
            "report": report,
        }
        audit_event(
            "task_run",
            approval_id=approval_id,
            success=report.success,
            files_changed=report.patch.files_changed if report.patch else [],
        )
        return {
            "approval_id": approval_id,
            "task": report.task,
            "plan": report.plan,
            "citations": [c.__dict__ for c in report.retrieval.citations] if report.retrieval else [],
            "diff": report.patch.diff if report.patch else "",
            "files_changed": report.patch.files_changed if report.patch else [],
            "sandbox": self._sandbox_summary(report),
            "critique": report.critique,
            "success": report.success,
            "requires_approval": report.requires_approval,
            "notes": report.notes,
        }

    def _sandbox_summary(self, report: AgentReport) -> dict:
        s = report.sandbox_result
        if not s:
            return {"ran": False}
        return {
            "ran": True,
            "exit_code": s.exit_code,
            "timed_out": s.timed_out,
            "truncated": s.truncated,
            "duration_seconds": s.duration_seconds,
            "network_enabled": s.network_enabled,
            "stdout_tail": s.stdout[-1500:],
            "stderr_tail": s.stderr[-1500:],
        }

    # -- approval gate ------------------------------------------------------
    def decide(self, approval_id: str, approve: bool) -> dict:
        entry = self._pending.pop(approval_id, None)
        if entry is None:
            raise KeyError("unknown or already-decided approval_id")
        report: AgentReport = entry["report"]
        audit_event("approval_decision", approval_id=approval_id, approved=approve)
        if not approve:
            return {"approval_id": approval_id, "applied": False, "status": "rejected"}
        applied_files = self._apply_patch(approval_id, report)
        return {
            "approval_id": approval_id,
            "applied": True,
            "status": "applied",
            "files": applied_files,
            "workspace": str(self._approved_workspace(approval_id)) if applied_files else None,
        }

    def _approved_workspace(self, approval_id: str) -> Path:
        return Path(settings.data_dir) / "approved_patches" / approval_id

    def _apply_patch(self, approval_id: str, report: AgentReport) -> List[str]:
        """
        Materialise the approved patch's final file contents to a real,
        durable, per-approval directory on disk. Before this call nothing the
        agent proposed exists outside its throwaway sandbox tempdir (already
        cleaned up); only an explicit human approval causes anything to be
        written -- this is the "no generated change may be silently applied"
        guarantee made concrete rather than a status flag that flips over an
        in-memory object no one can observe.
        """
        if not report.patch or not report.patch.files_changed:
            return []
        workspace = self._approved_workspace(approval_id)
        workspace.mkdir(parents=True, exist_ok=True)
        workspace_real = workspace.resolve(strict=True)
        written: List[str] = []
        for filename in report.patch.files_changed:
            rel = Path(filename)
            if rel.is_absolute() or ".." in rel.parts:
                # Defensive: a future (e.g. LLM-backed) patch generator could
                # propose an unsafe filename; never let approval turn that
                # into a write outside the approved workspace jail.
                continue
            target = (workspace_real / rel).resolve()
            if workspace_real not in target.parents and target != workspace_real:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report.patch.new_contents.get(filename, ""), encoding="utf-8")
            written.append(filename)
        audit_event("patch_applied", approval_id=approval_id, files=written, workspace=str(workspace))
        return written


# Process-wide singleton.
services = Services()
