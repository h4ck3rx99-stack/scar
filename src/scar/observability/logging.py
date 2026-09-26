"""structlog configuration: JSON logs in rotating files, concise console output.

A redaction processor runs on every record. Model reasoning is never logged.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog

from scar.security.redaction import global_redactor

_CONFIGURED = False
_FORBIDDEN_KEYS = {"reasoning", "chain_of_thought", "thinking", "reasoning_content"}


def _redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key in _FORBIDDEN_KEYS:
            event_dict[key] = "[omitted]"
    return global_redactor().redact_obj(event_dict)


def configure_logging(log_dir: Path, level: str = "INFO", console: bool = False, debug: bool = False) -> Path:
    """Configure structlog + stdlib logging. Returns the log file path."""
    global _CONFIGURED
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "scar.jsonl"

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_processor,
    ]

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_scar", False):
            root.removeHandler(h)
            h.close()

    file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(ensure_ascii=False),
            foreign_pre_chain=shared,
        )
    )
    file_handler._scar = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
                foreign_pre_chain=shared,
            )
        )
        console_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
        console_handler._scar = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)

    root.setLevel(logging.DEBUG if debug else getattr(logging, level.upper(), logging.INFO))
    for noisy in ("httpx", "httpcore", "asyncio", "urllib3", "comtypes", "faiss", "PIL", "watchdog", "trafilatura",
                  "hpack", "websockets", "primp", "fastembed", "telethon"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True
    return log_file


def is_configured() -> bool:
    return _CONFIGURED
