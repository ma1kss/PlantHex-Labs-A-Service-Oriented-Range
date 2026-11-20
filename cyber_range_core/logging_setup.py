from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

# Propagated across async tasks via contextvars
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(corr_id: str | None) -> None:
    _correlation_id.set(corr_id)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def _add_correlation(_logger, _method, event_dict):
    corr = _correlation_id.get()
    if corr:
        event_dict["correlation_id"] = corr
    return event_dict


def configure_logging(service_name: str, level: str = "INFO", fmt: str = "json") -> None:
    """Call once during app startup."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _add_correlation,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.processors.CallsiteParameterAdder(
                parameters=[structlog.processors.CallsiteParameter.MODULE],
            ),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bind the service name into the default context so every log line carries it
    structlog.contextvars.bind_contextvars(service=service_name)

    # Silence overly chatty libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aio_pika").setLevel(logging.INFO)
    logging.getLogger("aiormq").setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
