"""Structured (JSON) logging helper for the storage layer (Epic 2 D1).

Provides a JSON formatter that redacts known-sensitive keys so API keys and
passwords never appear in logs.
"""
from __future__ import annotations

import json
import logging
import re
import sys

_REDACT_KEYS = {"api_key", "password", "passwd", "token", "secret", "authorization"}
_REDACTED = "[REDACTED]"

# heuristics to scrub secrets from free-text messages (allow hyphens/underscores)
_SECRET_PATTERNS = [
    re.compile(r"\b(sk|pk|api|bearer|token)[-_]?[A-Za-z0-9_-]{8,}\b", re.I),
    re.compile(r"\b[0-9a-f]{40}\b", re.I),  # 40-hex keys
]


def _redact_value(key: str, value):
    if key.lower() in _REDACT_KEYS:
        return _REDACTED
    if isinstance(value, str):
        for pat in _SECRET_PATTERNS:
            value = pat.sub(_REDACTED, value)
        return value
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # scrub free-text message for secrets
        for pat in _SECRET_PATTERNS:
            entry["message"] = pat.sub(_REDACTED, entry["message"])
        for key, val in record.__dict__.items():
            if key in ("message", "asctime", "args", "exc_info", "exc_text",
                       "stack_info", "levelname", "name", "created", "msecs",
                       "relativeCreated", "thread", "threadName", "process",
                       "processName", "filename", "module", "funcName",
                       "lineno", "pathname", "taskName", "levelno", "msg",
                       "getMessage"):
                continue
            entry[key] = _redact_value(key, val)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def get_structured_logger(name: str, capture: bool = False) -> logging.Logger:
    """Return a logger emitting JSON lines.

    ``capture`` bypasses the stderr StreamHandler (used in tests).
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers and not capture:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
