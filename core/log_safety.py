"""Helpers for keeping sensitive data out of logs.

Endpoint URLs configured by admins can embed credentials in the userinfo
(``https://user:pass@host``) or query string (``?api_key=...``). Logging them
raw leaks those secrets, so route/diagnostic logs run URLs through
``redact_url`` first. Reconstructing the URL without userinfo/query/fragment
also doubles as a sanitizer barrier for CodeQL's clear-text-logging query.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from urllib.parse import urlparse, urlunparse


CAPABILITY_DIAGNOSTICS_LOGGER = "src.model_capability_readers"
UVICORN_LOGGER_NAMES = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "uvicorn.asgi",
)

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "FATAL": logging.CRITICAL,
    "CRITICAL": logging.CRITICAL,
}


def application_log_settings(value: object) -> tuple[int, bool]:
    """Return the safe app level and whether scoped capability debug is on.

    Application-wide DEBUG logging can expose request bodies, provider
    responses, or credentials from unrelated libraries.  The model capability
    catalog has a deliberately bounded DEBUG summary, so a DEBUG request is
    translated into INFO for the application and enabled only for that logger.
    Unknown values also fail closed to INFO.
    """

    requested = _LOG_LEVELS.get(str(value or "INFO").strip().upper(), logging.INFO)
    return max(requested, logging.INFO), requested == logging.DEBUG


def configure_uvicorn_log_levels(application_level: int) -> None:
    """Apply the mapped app level to Uvicorn's non-propagating loggers.

    External entrypoints configure these loggers before importing ``app`` and
    otherwise bypass the root logger's level and scoped diagnostics filter.
    """

    for logger_name in UVICORN_LOGGER_NAMES:
        logging.getLogger(logger_name).setLevel(application_level)


def uvicorn_log_config(application_level: int) -> dict:
    """Return a Uvicorn config that preserves the mapped level on direct runs."""

    from uvicorn.config import LOGGING_CONFIG

    config = deepcopy(LOGGING_CONFIG)
    loggers = config.setdefault("loggers", {})
    for logger_name in UVICORN_LOGGER_NAMES:
        loggers.setdefault(logger_name, {})["level"] = application_level
    return config


class ScopedDiagnosticsFilter(logging.Filter):
    """Allow normal application records plus one explicitly scoped DEBUG log."""

    def __init__(
        self,
        application_level: int,
        *,
        capability_debug: bool = False,
    ) -> None:
        super().__init__()
        self.application_level = application_level
        self.capability_debug = capability_debug

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= self.application_level:
            return True
        return (
            self.capability_debug
            and record.levelno >= logging.DEBUG
            and record.name == CAPABILITY_DIAGNOSTICS_LOGGER
        )


def redact_url(url: str) -> str:
    """Return a URL safe for logs by removing userinfo and query/fragment.

    Keeps scheme, host, port and path so logs stay useful for debugging.
    """
    try:
        parsed = urlparse(url or "")
        host = parsed.hostname or ""
        if ":" in host:  # IPv6 literal — re-bracket so host:port stays unambiguous
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
    except Exception:
        return "<endpoint>"
