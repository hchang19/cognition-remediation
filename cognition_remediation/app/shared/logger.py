"""Structured JSON logger. Writes one JSON object per line to stdout.

Pass ``log_file`` to also write the same JSON to a file — used by the
orchestrator to maintain a persistent ``cognition.log`` alongside stdout.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from typing import Any

_STD_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str, log_file: str | None = None) -> logging.Logger:
    """Return a logger writing structured JSON lines to stdout.

    If ``log_file`` is provided, a ``FileHandler`` writing the same JSON
    format is added. Idempotent — repeated calls with the same ``name``
    return the same logger without adding duplicate handlers.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    formatter = _JsonFormatter()
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
