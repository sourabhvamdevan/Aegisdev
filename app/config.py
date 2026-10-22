"""Central configuration, loaded from environment with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    app_name: str = "AegisDev"
    # ":memory:" keeps everything in RAM -> safe on ephemeral free-tier disks.
    db_path: str = os.environ.get("AEGISDEV_DB", os.environ.get("SENTINELFORGE_DB", ":memory:"))  # old name still honoured
    embedding_backend: str = os.environ.get("EMBEDDING_BACKEND", "auto")
    # Where uploaded/sample docs live (ephemeral tmp by default).
    data_dir: str = os.environ.get("AEGISDEV_DATA", os.environ.get("SENTINELFORGE_DATA", "/tmp/aegisdev_data"))  # noqa: S108 (ephemeral tmp is intended on free-tier hosts)
    # CORS: locked to explicit origins; "*" only if the operator opts in.
    cors_origins: str = os.environ.get("CORS_ORIGINS", "http://localhost:8000")
    max_upload_bytes: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
    rate_limit_per_minute: int = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    provider_mode: str = os.environ.get("PROVIDER_MODE", "local")  # local | external

    # Patch generation backend for the coding agent:
    #   "builtin" -> deterministic placeholder (offline, zero-cost, default)
    #   "ollama"  -> local LLM via Ollama (privacy-first, still zero external cost)
    patch_generator: str = os.environ.get("PATCH_GENERATOR", "builtin")
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
    ollama_timeout: int = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

    # Retrieval: hybrid (BM25 + vector fused with RRF) | vector | lexical
    retrieval_mode: str = os.environ.get("RETRIEVAL_MODE", "hybrid")

    @property
    def cors_list(self):
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
