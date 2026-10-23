"""Tests for model_context.py — local endpoint detection, token estimation, known model lookup."""

import sys
import types

import pytest
import httpx

import src.model_context as model_context
from src import endpoint_resolver
from src.model_context import is_local_endpoint, estimate_tokens, _lookup_known


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)


class _ModelEndpoint:
    is_enabled = _Column("is_enabled")


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for condition in conditions:
            if isinstance(condition, tuple) and condition[0] == "eq":
                _, field, value = condition
                self.rows = [row for row in self.rows if getattr(row, field) == value]
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows)

    def close(self):
        pass


def _install_endpoint_db(monkeypatch, rows):
    mod = types.ModuleType("core.database")
    mod.ModelEndpoint = _ModelEndpoint
    mod.SessionLocal = lambda: _Db(rows)
    monkeypatch.setitem(sys.modules, "core.database", mod)


class TestIsLocalEndpoint:
    def test_localhost(self):
        assert is_local_endpoint("http://localhost:5000/v1/chat/completions") is True

    def test_loopback_ipv4(self):
        assert is_local_endpoint("http://127.0.0.1:8080/v1/chat/completions") is True

    def test_private_192_168(self):
        assert is_local_endpoint("http://192.168.1.1:11434/v1/chat/completions") is True

    def test_private_10(self):
        assert is_local_endpoint("http://10.0.0.5:8000/v1/chat/completions") is True

    @pytest.mark.parametrize("host", [
        "10.example-cloud.com",
        "172.16.example-cloud.com",
        "192.168.example-cloud.com",
    ])
    def test_private_prefix_dns_names_are_remote(self, host):
        assert is_local_endpoint(f"https://{host}/v1/chat/completions") is False

    def test_tailscale_100(self):
        # 100.64.0.0/10 is the CGNAT range Tailscale uses.
        assert is_local_endpoint("http://100.64.0.1:5000/v1/chat/completions") is True

    def test_configured_tailscale_proxy_is_remote(self, monkeypatch):
        _install_endpoint_db(monkeypatch, [
            types.SimpleNamespace(
                base_url="http://100.117.136.97:34521/v1",
                endpoint_kind="proxy",
                api_key="fake-key",
                is_enabled=True,
            )
        ])

        assert is_local_endpoint("http://100.117.136.97:34521/v1/chat/completions") is False

    def test_openai_is_remote(self):
        assert is_local_endpoint("https://api.openai.com/v1/chat/completions") is False

    def test_anthropic_is_remote(self):
        assert is_local_endpoint("https://api.anthropic.com/v1/messages") is False

    def test_empty_url(self):
        assert is_local_endpoint("") is False

    def test_malformed_url(self):
        assert is_local_endpoint("not-a-url") is False


class TestEstimateTokens:
    def test_empty_list(self):
        assert estimate_tokens([]) == 0

    def test_single_short_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        tokens = estimate_tokens(messages)
        # 4 overhead + int(5 * 0.3) = 4 + 1 = 5
        assert tokens == 5

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi there"},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0
        # Each message adds 4 overhead + chars * 0.3
        assert tokens == 4 + int(16 * 0.3) + 4 + int(8 * 0.3)

    def test_multimodal_content_list(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        tokens = estimate_tokens(messages)
        # 4 overhead + int(19 * 0.3) for the text item; image_url is ignored
        assert tokens == 4 + int(19 * 0.3)

    def test_missing_content_key(self):
        messages = [{"role": "assistant"}]
        tokens = estimate_tokens(messages)
        # 4 overhead + 0 content
        assert tokens == 4

    def test_scales_with_length(self):
        short = estimate_tokens([{"role": "user", "content": "short"}])
        long_text = "a" * 10000
        long = estimate_tokens([{"role": "user", "content": long_text}])
        assert long > short * 10

    def test_cjk_text_is_not_estimated_with_latin_character_ratio(self):
        cjk = estimate_tokens([{"role": "user", "content": "诉讼时效期间" * 20}])
        latin = estimate_tokens([{"role": "user", "content": "a" * 120}])

        assert cjk == 124
        assert cjk > latin * 2

    def test_binary_replacement_text_is_not_estimated_as_latin(self):
        replacement_dump = estimate_tokens([
            {"role": "tool", "content": "\ufffd" * 1000},
        ])

        assert replacement_dump == 1004


class TestLookupKnown:
    def test_claude_sonnet(self):
        assert _lookup_known("claude-sonnet-4-5") == 200000

    def test_gpt4o(self):
        assert _lookup_known("gpt-4o") == 128000

    def test_deepseek_r1(self):
        assert _lookup_known("deepseek-r1") == 64000

    def test_deepseek_flash_provider_alias(self):
        assert _lookup_known("deepseek-flash") == 64000

    def test_gemini_pro(self):
        assert _lookup_known("gemini-2.5-pro") == 1048576

    def test_unknown_model(self):
        assert _lookup_known("totally-unknown-model-xyz") is None

    def test_namespaced_model(self):
        """Models prefixed with provider/ should still match."""
        result = _lookup_known("openrouter/deepseek-r1")
        assert result == 64000

    def test_model_with_tag(self):
        """Models with :free or :extended suffixes should still match."""
        result = _lookup_known("deepseek-r1:free")
        assert result == 64000

    def test_o1_mini_not_shadowed_by_o1(self):
        """'o1' (200k) precedes 'o1-mini' (128k) in the table; longest match wins."""
        assert _lookup_known("o1-mini") == 128000

    def test_o1_full(self):
        assert _lookup_known("o1") == 200000

    def test_gpt4o_mini_not_shadowed_by_gpt4(self):
        assert _lookup_known("gpt-4o-mini") == 128000

    def test_gpt4_base(self):
        assert _lookup_known("gpt-4") == 8192


class _FakeResp:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


class TestGetContextLength:
    def setup_method(self):
        model_context._context_cache.clear()
        model_context._catalog_ctx_cache.clear()

    def test_local_endpoint_requeries_same_model_after_restart(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return (8192, True) if len(calls) == 1 else (27000, True)

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "http://127.0.0.1:8000/v1/chat/completions"
        model = "Qwen/Qwen3-14B"

        first = model_context.get_context_length(endpoint, model)
        second = model_context.get_context_length(endpoint, model)

        assert first == 8192
        assert second == 27000
        assert len(calls) == 2

    def test_remote_endpoint_keeps_cached_context(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return (200000, True) if len(calls) == 1 else (12345, True)

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "https://api.openai.com/v1/chat/completions"
        model = "gpt-5"

        first = model_context.get_context_length(endpoint, model)
        second = model_context.get_context_length(endpoint, model)

        assert first == 200000
        assert second == 200000
        assert len(calls) == 1

    def _proxy_db(self, monkeypatch):
        _install_endpoint_db(monkeypatch, [
            types.SimpleNamespace(
                base_url="http://100.117.136.97:34521/v1",
                endpoint_kind="proxy",
                api_key="fake-key",
                is_enabled=True,
            )
        ])

    def test_configured_proxy_known_model_skips_model_listing(self, monkeypatch):
        # A model covered by the known-context table must still resolve without
        # touching /models — the cheap path the proxy short-circuit exists for.
        self._proxy_db(monkeypatch)

        def fake_get(*args, **kwargs):
            raise AssertionError("/models must not be queried for a known proxy model")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        endpoint = "http://100.117.136.97:34521/v1/chat/completions"
        assert model_context.get_context_length(endpoint, "gpt-4o") == 128000

    def test_configured_proxy_unknown_model_reads_catalog_context(self, monkeypatch):
        # A model missing from the known table (e.g. a new OpenRouter model)
        # must report the catalog's real window, not the bare default (#4886).
        # The catalog is fetched once per endpoint and reused for other models.
        self._proxy_db(monkeypatch)
        fetches = []

        def fake_get(url, *args, **kwargs):
            fetches.append(url)
            return _FakeResp({"data": [
                {"id": "owl-alpha", "context_length": 1048576},
                {"id": "tiny-proxy-model", "context_length": 8192},
            ]})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        endpoint = "http://100.117.136.97:34521/v1/chat/completions"
        assert model_context.get_context_length(endpoint, "owl-alpha") == 1048576
        # A second unknown model on the same endpoint reuses the cached catalog.
        assert model_context.get_context_length(endpoint, "tiny-proxy-model") == 8192
        assert len(fetches) == 1

    def test_configured_proxy_unknown_model_falls_back_to_default(self, monkeypatch):
        # If the catalog can be read but doesn't list the model, keep the
        # conservative default rather than guessing.
        self._proxy_db(monkeypatch)

        def fake_get(url, *args, **kwargs):
            return _FakeResp({"data": [{"id": "some-other-model", "context_length": 4096}]})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        endpoint = "http://100.117.136.97:34521/v1/chat/completions"
        assert model_context.get_context_length(endpoint, "absent-model") == model_context.DEFAULT_CONTEXT

    def test_configured_proxy_catalog_fetch_failure_uses_default(self, monkeypatch):
        # A failed/unreachable catalog must not raise — fall back to the default.
        self._proxy_db(monkeypatch)

        def fake_get(url, *args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        endpoint = "http://100.117.136.97:34521/v1/chat/completions"
        assert model_context.get_context_length(endpoint, "unknown-proxy-model") == model_context.DEFAULT_CONTEXT


def _endpoint_row(base_url, **overrides):
    return types.SimpleNamespace(**{
        "base_url": base_url,
        "endpoint_kind": "auto",
        "api_key": "probe-test-key",
        "provider_auth_id": None,
        "is_enabled": True,
        **overrides,
    })


@pytest.mark.parametrize("probe", ["slots", "props", "models"])
@pytest.mark.parametrize("kind", ["auto", "local"])
def test_protected_local_endpoint_uses_served_context(monkeypatch, probe, kind):
    base = "http://127.0.0.1:8080/tenant/server/v1"
    _install_endpoint_db(monkeypatch, [_endpoint_row(base, endpoint_kind=kind)])
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    requests = []

    def respond(request):
        requests.append(request)
        if request.headers.get("Authorization") != "Bearer probe-test-key":
            return httpx.Response(401)
        if request.url.path.endswith("/" + probe):
            payload = {
                "slots": [{"n_ctx": 8192}],
                "props": {"default_generation_settings": {"n_ctx": 8192}},
                "models": {"data": [{"id": "qwen3", "context_length": 8192}]},
            }[probe]
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(base + "/chat/completions", "qwen3") == (8192, True)

    assert requests
    assert all(request.headers["Authorization"] == "Bearer probe-test-key" for request in requests)
    assert [request.url.path for request in requests] == {
        "slots": ["/tenant/server/slots"],
        "props": ["/tenant/server/slots", "/tenant/server/props"],
        "models": ["/tenant/server/slots", "/tenant/server/props", "/tenant/server/v1/models"],
    }[probe]


@pytest.mark.parametrize("row", [
    None,
    _endpoint_row("http://127.0.0.1:8080/tenant/v1", is_enabled=False),
    _endpoint_row("http://127.0.0.2:8080/tenant/v1"),
    _endpoint_row("http://127.0.0.1:8081/tenant/v1"),
    _endpoint_row("https://127.0.0.1:8080/tenant/v1"),
    _endpoint_row("http://127.0.0.1:8080/v1"),
    _endpoint_row("http://127.0.0.1:8080/tenant/v1/other"),
    _endpoint_row("http://127.0.0.1:8080/tenant/v1", provider_auth_id="account-session"),
], ids=["unconfigured", "disabled", "host", "port", "scheme", "parent-prefix", "child-prefix", "account-auth"])
def test_context_probe_does_not_borrow_unrelated_credentials(monkeypatch, row):
    _install_endpoint_db(monkeypatch, [] if row is None else [row])
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(
            "http://127.0.0.1:8080/tenant/v1/chat/completions", "qwen3",
        ) == (131072, True)

    assert len(requests) == 3
    assert all("Authorization" not in request.headers for request in requests)


def test_protected_proxy_catalog_uses_registered_auth(monkeypatch):
    base = "https://proxy.test/tenant/v1"
    _install_endpoint_db(monkeypatch, [_endpoint_row(base, endpoint_kind="proxy")])
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    monkeypatch.setattr(model_context, "_catalog_ctx_cache", {})
    requests = []

    def respond(request):
        requests.append(request)
        if request.headers.get("Authorization") != "Bearer probe-test-key":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": [{"id": "new-model", "context_length": 32768}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(base, "new-model") == (32768, True)

    assert [str(request.url) for request in requests] == [base + "/models"]


def test_context_probe_matches_normalized_origin_and_base(monkeypatch):
    _install_endpoint_db(monkeypatch, [_endpoint_row("http://LOCALHOST:80/tenant/v1/")])
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[{"n_ctx": 8192}])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(
            "http://localhost/tenant/v1/chat/completions", "qwen3",
        ) == (8192, True)

    assert requests[0].headers["Authorization"] == "Bearer probe-test-key"


@pytest.mark.parametrize("second", [
    {"api_key": "different-registration-key"},
    {"provider_auth_id": "account-session"},
    {"owner": "another-owner"},
    {"endpoint_kind": "proxy"},
])
@pytest.mark.parametrize("reverse", [False, True])
def test_context_probe_does_not_guess_between_conflicting_registrations(monkeypatch, second, reverse):
    base = "http://127.0.0.1:8080/tenant/v1"
    rows = [_endpoint_row(base), _endpoint_row(base, **second)]
    _install_endpoint_db(monkeypatch, rows[::-1] if reverse else rows)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._get_context_probe(base, base + "/models").status_code == 401

    assert "Authorization" not in requests[0].headers


def test_context_probe_keeps_auth_for_equivalent_registration_aliases(monkeypatch):
    base = "http://127.0.0.1:8080/tenant/v1"
    _install_endpoint_db(monkeypatch, [_endpoint_row(base), _endpoint_row(base + "/")])
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._get_context_probe(base, base + "/models").status_code == 200

    assert requests[0].headers["Authorization"] == "Bearer probe-test-key"


def test_context_probes_do_not_follow_redirects(monkeypatch):
    base = "http://127.0.0.1:8080/tenant/v1"
    _install_endpoint_db(monkeypatch, [_endpoint_row(base)])
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.test/v1/models"})

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(base, "qwen3") == (131072, True)

    assert len(requests) == 3
    assert all(request.url.host == "127.0.0.1" for request in requests)


def test_known_public_api_keeps_fallback_when_probe_requires_auth(monkeypatch):
    _install_endpoint_db(monkeypatch, [])
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(
            "https://api.openai.com/v1/chat/completions", "gpt-4o",
        ) == (128000, True)

    assert [str(request.url) for request in requests] == ["https://api.openai.com/v1/models"]
    assert "Authorization" not in requests[0].headers


@pytest.mark.parametrize("models_url", [
    "http://127.0.0.2:8080/tenant/v1/models",
    "http://127.0.0.1:8080/other/v1/models",
])
def test_derived_models_url_cannot_receive_credentials_for_another_endpoint(monkeypatch, models_url):
    base = "http://127.0.0.1:8080/tenant/v1"
    _install_endpoint_db(monkeypatch, [_endpoint_row(base)])
    monkeypatch.setattr(endpoint_resolver, "build_models_url", lambda url: models_url)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(model_context.httpx, "get", client.get)
        assert model_context._query_context_length(base, "qwen3") == (131072, True)

    assert str(requests[-1].url) == models_url
    assert "Authorization" not in requests[-1].headers
