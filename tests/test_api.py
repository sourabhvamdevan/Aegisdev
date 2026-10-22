"""HTTP API tests via FastAPI TestClient (exercises the MCP-backed endpoints)."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status_reports_local_mode_and_backend():
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider_mode"] == "local"
    assert "embedding_backend" in body


def test_ingest_then_query_returns_citations():
    client.post("/api/ingest/text", json={"name": "api.md", "text": "patches require explicit human approval"})
    r = client.post("/api/query", json={"query": "human approval", "top_k": 3})
    assert r.status_code == 200
    assert "citations" in r.json()


def test_sandbox_endpoint_blocks_unsafe_command():
    r = client.post("/api/sandbox/run", json={"command": ["rm", "-rf", "/"]})
    assert r.status_code == 400
    assert "allowlist" in r.json()["detail"]


def test_sandbox_endpoint_runs_with_uploaded_files():
    # Regression: passing a `files` dict (serialized to a JSON string for the
    # MCP tool call) must not be mistaken by the MCP arg validator for a JSON
    # object matching the field's own type and rejected before ever reaching
    # the sandbox.
    r = client.post(
        "/api/sandbox/run",
        json={"files": {"solution.py": "print(2 + 2)"}, "command": ["python", "solution.py"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 0
    assert body["stdout"].strip() == "4"


def test_task_endpoint_runs_with_seed_files():
    r = client.post(
        "/api/task",
        json={"task": "add a solve function", "files": {"solve.py": "def solve(x):\n    return x\n"}},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_task_and_approval_flow():
    r = client.post("/api/task", json={"task": "add a solve function and a test"})
    assert r.status_code == 200
    body = r.json()
    assert body["requires_approval"] is True
    approval_id = body["approval_id"]
    d = client.post("/api/approve", json={"approval_id": approval_id, "approve": True})
    assert d.status_code == 200
    assert d.json()["applied"] is True


def test_unknown_approval_id_returns_404():
    d = client.post("/api/approve", json={"approval_id": "doesnotexist", "approve": True})
    assert d.status_code == 404


def test_approval_actually_persists_files_to_disk():
    # Regression: approval used to just flip an in-memory status flag with no
    # observable effect. "No generated change may be silently applied" must
    # cut both ways -- an *approved* change must actually land somewhere real.
    r = client.post("/api/task", json={"task": "add a solve function for disk-apply test"})
    assert r.status_code == 200
    body = r.json()
    approval_id = body["approval_id"]
    files_changed = body["files_changed"]
    assert files_changed

    workspace = Path(settings.data_dir) / "approved_patches" / approval_id
    assert not workspace.exists()  # nothing written before approval

    d = client.post("/api/approve", json={"approval_id": approval_id, "approve": True})
    assert d.status_code == 200
    decision = d.json()
    assert decision["applied"] is True
    assert decision["files"] == files_changed

    for name in files_changed:
        written = workspace / name
        assert written.is_file()
        assert written.read_text(encoding="utf-8") != ""


def test_rejected_approval_writes_nothing():
    r = client.post("/api/task", json={"task": "add a solve function for reject test"})
    approval_id = r.json()["approval_id"]
    d = client.post("/api/approve", json={"approval_id": approval_id, "approve": False})
    assert d.status_code == 200
    assert d.json() == {"approval_id": approval_id, "applied": False, "status": "rejected"}
    workspace = Path(settings.data_dir) / "approved_patches" / approval_id
    assert not workspace.exists()
