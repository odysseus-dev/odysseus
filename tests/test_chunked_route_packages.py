"""Regression coverage for the route modules split into Python packages."""

from __future__ import annotations


def _route_signatures(router):
    signatures = []
    for route in router.routes:
        methods = sorted(route.methods or ())
        signatures.extend((method, route.path) for method in methods)
    return signatures


def test_chunked_route_packages_register_expected_unique_routes(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_INPROCESS_POLLERS", "0")

    from routes.cookbook import setup_cookbook_routes
    from routes.chat import setup_chat_routes
    from routes.email import setup_email_routes
    from routes.model import setup_model_routes

    routers = {
        "chat": (setup_chat_routes(*([object()] * 6)), 8),
        "cookbook": (setup_cookbook_routes(), 17),
        "email": (setup_email_routes(), 54),
        "model": (setup_model_routes(object()), 21),
    }

    for name, (router, expected_count) in routers.items():
        assert len(router.routes) == expected_count, name
        signatures = _route_signatures(router)
        assert len(signatures) == len(set(signatures)), name


def test_chunked_route_packages_expose_downstream_imports(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_INPROCESS_POLLERS", "0")

    from routes.cookbook import setup_cookbook_routes
    from routes.chat import setup_chat_routes
    from routes.email import (
        SendEmailRequest,
        _decode_header,
        _get_email_config,
        _imap,
        _read_cache_get,
        _read_cache_key,
        _resolve_send_config,
        setup_email_routes,
    )
    from routes.model import (
        _invalidate_models_cache,
        _probe_endpoint,
        _visible_models,
        setup_model_routes,
    )

    exports = (
        setup_cookbook_routes,
        setup_chat_routes,
        SendEmailRequest,
        _decode_header,
        _get_email_config,
        _imap,
        _read_cache_get,
        _read_cache_key,
        _resolve_send_config,
        setup_email_routes,
        _invalidate_models_cache,
        _probe_endpoint,
        _visible_models,
        setup_model_routes,
    )
    assert all(callable(export) for export in exports)

    chat_globals = setup_chat_routes.__globals__
    assert {
        "_verify_session_owner",
        "_enforce_chat_privileges",
        "_resolve_research_endpoint",
        "_classify_tool_intent",
        "_owner_session_filter",
    } <= chat_globals.keys()

    setup_model_routes(object())
    _invalidate_models_cache()


def test_email_read_cache_export_uses_preview_cache_contract(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_INPROCESS_POLLERS", "0")

    import routes.email as email_routes

    router = email_routes.setup_email_routes()
    pool = router._email_pool
    key = pool["read_cache_key"]("account", "INBOX", "42", owner="alice") + (0,)
    cached = {"subject": "Cached", "from_address": "sender@example.com"}
    pool["read_cache_put"](key, cached)

    assert email_routes._read_cache_key(
        "account", "INBOX", "42", owner="alice"
    ) + (0,) == key
    assert email_routes._read_cache_get(key) == cached
