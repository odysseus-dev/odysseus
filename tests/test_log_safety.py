import logging

import pytest

from core.log_safety import (
    CAPABILITY_DIAGNOSTICS_LOGGER,
    ScopedDiagnosticsFilter,
    application_log_settings,
    configure_uvicorn_log_levels,
    redact_url,
)


def test_strips_userinfo():
    assert redact_url("https://user:pass@host.example/v1/models") == "https://host.example/v1/models"


def test_strips_query_and_fragment():
    assert redact_url("https://host.example/v1?api_key=secret#frag") == "https://host.example/v1"


def test_keeps_port_and_path():
    assert redact_url("http://host.example:8080/api/tags") == "http://host.example:8080/api/tags"


def test_ipv6_host_keeps_brackets():
    assert redact_url("https://user:pass@[2001:db8::1]:8443/v1") == "https://[2001:db8::1]:8443/v1"
    assert redact_url("https://[2001:db8::1]/v1") == "https://[2001:db8::1]/v1"


def test_no_credentials_passthrough():
    assert redact_url("https://host.example/v1/models") == "https://host.example/v1/models"


def test_empty_and_none():
    assert redact_url("") == ""
    assert redact_url(None) == ""


def test_garbage_does_not_raise():
    # urlparse is lenient; just assert no credential-looking userinfo survives.
    assert "@" not in redact_url("::::not a url::::")


@pytest.mark.parametrize(
    ("configured", "expected_level", "expected_capability_debug"),
    (
        ("DEBUG", logging.INFO, True),
        ("debug", logging.INFO, True),
        ("INFO", logging.INFO, False),
        ("WARNING", logging.WARNING, False),
        ("ERROR", logging.ERROR, False),
        ("CRITICAL", logging.CRITICAL, False),
        ("not-a-level", logging.INFO, False),
        (None, logging.INFO, False),
    ),
)
def test_application_log_settings_scope_debug_and_fail_closed(
    configured,
    expected_level,
    expected_capability_debug,
):
    assert application_log_settings(configured) == (
        expected_level,
        expected_capability_debug,
    )


def test_configure_uvicorn_log_levels_clamps_non_propagating_loggers():
    logger_names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    previous_levels = {
        name: logging.getLogger(name).level for name in logger_names
    }
    try:
        for name in logger_names:
            logging.getLogger(name).setLevel(logging.DEBUG)

        configure_uvicorn_log_levels(logging.ERROR)

        assert all(
            logging.getLogger(name).level == logging.ERROR for name in logger_names
        )
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)


def _record(name: str, level: int) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, "message", (), None)


def test_scoped_diagnostics_filter_allows_only_bounded_debug_logger():
    log_filter = ScopedDiagnosticsFilter(logging.INFO, capability_debug=True)

    assert log_filter.filter(_record(CAPABILITY_DIAGNOSTICS_LOGGER, logging.DEBUG))
    assert log_filter.filter(_record("unrelated.library", logging.INFO))
    assert not log_filter.filter(_record("unrelated.library", logging.DEBUG))
    assert not log_filter.filter(_record(f"{CAPABILITY_DIAGNOSTICS_LOGGER}.raw", logging.DEBUG))


def test_scoped_diagnostics_filter_respects_higher_application_level():
    log_filter = ScopedDiagnosticsFilter(logging.WARNING, capability_debug=False)

    assert log_filter.filter(_record("application", logging.WARNING))
    assert not log_filter.filter(_record("application", logging.INFO))
    assert not log_filter.filter(_record(CAPABILITY_DIAGNOSTICS_LOGGER, logging.DEBUG))
