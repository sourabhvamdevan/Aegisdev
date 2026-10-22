"""
AegisDev application entrypoint.

Wires together the HTTP API, the static web UI, and the MCP HTTP endpoint, with
secure defaults: explicit CORS origins, a simple in-memory rate limiter,
per-request IDs, safe error handling, and sample-data bootstrap that does not
depend on persistent disk.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router as api_router
from .config import settings
from .logging_config import audit_event, configure_logging, log, new_request_id

configure_logging()


def _bootstrap_samples() -> None:
    """Ingest safe sample data so the deployed demo works with no persistent disk."""
    from .services import services

    samples = {
        "welcome.md": (
            "# AegisDev\n"
            "AegisDev is a privacy-first AI developer platform. The sandbox "
            "disables network by default and enforces CPU, memory and process "
            "limits. Retrieval returns citations with filename and line range."
        ),
        "security.md": (
            "# Security model\n"
            "Untrusted document content is fenced and never executed as "
            "instructions. Applying a patch requires explicit human approval. "
            "Commands run against an allowlist inside a workspace jail."
        ),
    }
    for name, content in samples.items():
        try:
            services.ingest_text(name, content)
        except Exception as e:  # keep startup resilient
            log.warning(f"sample ingest failed for {name}: {e}")
    audit_event("bootstrap", sources=list(samples.keys()))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_samples()
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    version="0.1.0",
    description="Privacy-first AI developer platform: MCP + local RAG + sandboxed agent.",
    lifespan=lifespan,
)

# CORS: explicit origins only (no wildcard unless the operator sets it).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

# --- naive in-memory rate limiter -------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = new_request_id()
    # rate limit per client IP over a 60s sliding window
    client = request.client.host if request.client else "unknown"
    now = time.time()
    window = _hits[client]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= settings.rate_limit_per_minute:
        audit_event("rate_limited", client=client)
        return JSONResponse(
            status_code=429,
            content={"error": "rate limit exceeded", "request_id": rid},
        )
    window.append(now)

    try:
        response = await call_next(request)
    except Exception:  # safe error handling: never leak stack traces
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={"error": "internal error", "request_id": rid},
        )
    response.headers["X-Request-ID"] = rid
    return response


app.include_router(api_router, prefix="/api")

# --- static web UI -----------------------------------------------------------
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")


@app.get("/")
async def index():
    idx = _WEB_DIR / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    return {"app": settings.app_name, "docs": "/docs", "ui": "/ui"}


# --- MCP HTTP endpoint -------------------------------------------------------
# Mounted so an external MCP client can discover/invoke tools over HTTP too.
try:
    from .mcp_server.server import build_http_app

    app.mount("/mcp", build_http_app())
except Exception as e:  # pragma: no cover - keep API up even if mount fails
    log.warning(f"MCP HTTP mount skipped: {e}")
