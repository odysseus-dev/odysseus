"""Tests for searxng_health — probe logic, status classification, and sanitization."""
import types

import pytest

from src import service_health as sh


def _resp(status_code):
    return types.SimpleNamespace(status_code=status_code)


def _raise(*_a, **_k):
    raise RuntimeError("connection refused")


def test_searxng_disabled_when_other_provider():
    s = sh.searxng_health({"search_provider": "brave"})
    assert s["status"] == sh.DISABLED


def test_searxng_ok_on_healthz():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=lambda url, timeout: _resp(200),
    )
    assert s["status"] == sh.OK
    assert s["meta"]["probed"] == "/healthz"


def test_searxng_ok_on_root_fallback():
    def getter(url, timeout):
        return _resp(404) if url.endswith("/healthz") else _resp(200)

    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=getter,
    )
    assert s["status"] == sh.OK
    assert s["meta"]["probed"] == "/"


def test_searxng_down_on_exception():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=_raise,
    )
    assert s["status"] == sh.DOWN


def test_searxng_down_on_5xx():
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://sx:8080"},
        http_get=lambda url, timeout: _resp(502),
    )
    assert s["status"] == sh.DOWN


def test_searxng_meta_redacts_instance_url():
    s = sh.searxng_health(
        {"search_provider": "searxng",
         "search_url": "http://user:s3cr3t@searx.local:8080/?token=zzz"},
        http_get=lambda url, timeout: _resp(200),
    )
    blob = repr(s)
    assert "s3cr3t" not in blob and "zzz" not in blob and "user:" not in blob
    assert s["meta"]["instance"] == "http://searx.local:8080"


def test_searxng_down_uses_error_category_not_raw_exception():
    def boom(url, timeout):
        raise RuntimeError("failed connecting to http://user:pw@searx.local secret-token")
    s = sh.searxng_health(
        {"search_provider": "searxng", "search_url": "http://searx.local"},
        http_get=boom,
    )
    assert s["status"] == sh.DOWN
    assert s["meta"]["error"] == "error"           # controlled category token
    assert "secret-token" not in repr(s) and "pw@" not in repr(s)
