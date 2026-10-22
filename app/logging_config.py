"""
Structured JSON logging with request IDs and a dedicated audit channel.

Two loggers:
  * "aegisdev"        -> general structured app logs
  * "aegisdev.audit"  -> security-relevant events (sandbox runs, approvals,
                              injection flags). Kept separate so audit trails are
                              easy to filter and cannot be drowned out by noise.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)  # type: ignore[attr-defined]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("aegisdev")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


log = logging.getLogger("aegisdev")
audit = logging.getLogger("aegisdev.audit")


def audit_event(event: str, **fields) -> None:
    record = logging.LogRecord(
        name="aegisdev.audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=event,
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"event": event, **fields}  # type: ignore[attr-defined]
    for h in logging.getLogger("aegisdev").handlers:
        h.emit(record)
