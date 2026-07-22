"""Structured logging via structlog — JSON in production, console in dev.

Usage:
    from nous.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("task_completed", symbols=265, duration_ms=1234)
"""

from __future__ import annotations

import logging as stdlib_logging
import os
import sys

import structlog


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
) -> None:
    """Configure structlog for the entire application.

    Called once at application startup from ``cli.py`` or ``api/main.py``.

    Args:
        level: Log level (DEBUG/INFO/WARNING/ERROR).
        json_format: If True, output JSON (for production/ECS).
                     If False, output colored console (for development).
    """
    # Set stdlib log level first
    stdlib_logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(stdlib_logging, level.upper()),
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared_processors = [
        structlog.stdlib.filter_by_level,
        timestamper,
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
    ]

    if json_format:
        structlog.configure(
            processors=shared_processors + [structlog.processors.JSONRenderer()],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        structlog.configure(
            processors=shared_processors + [structlog.dev.ConsoleRenderer(colors=True)],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically ``__name__``).

    Returns:
        A bound logger ready for structured logging.
    """
    return structlog.get_logger(name or "nous")


# Initialize with defaults on import
_log_level = os.environ.get("NOUS_LOG_LEVEL", "INFO")
_json_format = os.environ.get("NOUS_LOG_FORMAT", "json") == "json"
setup_logging(level=_log_level, json_format=_json_format)
