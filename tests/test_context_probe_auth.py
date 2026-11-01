"""Regression for #6668: the context-window probe must authenticate.

A key-protected OpenAI-compatible server answers 401 to an anonymous /models, so
the lookup fell back to the static table even though the server reports its real
window. Two causes had to go together: the probes sent no key, and an endpoint on
Auto with a key and a /v1 base was classified "proxy", which skips every probe.
"""

import asyncio
import sys
import types

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src import endpoint_resolver, model_context as mc
from src.agent_runtime import context_resolution as cr

KEY = "fake-test-key"
MODEL = "Qwen3.8-27B"  # 131072 in the known table by substring.
UNKNOWN_MODEL = "owl-alpha"


class _Column:
    def __eq__(self, value):
        return True


class _ModelEndpoint:
    is_enabled = _Column()


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        return _Query([row for row in self.rows if row.is_enabled])

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows)

    def close(self):
        pass


def _row(base_url, kind="auto", api_key=KEY, **extra):
    return types.SimpleNamespace(
        base_url=base_url, endpoint_kind=kind, api_key=api_key, is_enabled=True, **extra,
    )


def _install_rows(monkeypatch, *rows):
    module = types.ModuleType("core.database")
    module.ModelEndpoint = _ModelEndpoint
    module.SessionLocal = lambda: _Db(list(rows))
    monkeypatch.setitem(sys.modules, "core.database", module)


class _KeyedServer:
    """Answers like the server in the report: 401 unless the key is presented."""

    def __init__(self, monkeypatch, window=204800, key=KEY, model=MODEL, n_ctx=None):
        self.window, self.key, self.model, self.n_ctx = window, key, model, n_ctx
        self.calls = []
        monkeypatch.setattr(mc.httpx, "get", self.get)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        request = httpx.Request("GET", url)
        headers = kwargs.get("headers") or {}
        if self.key and headers.get("Authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"code": "invalid_api_key"}}, request=request)
        if url.endswith("/slots") and self.n_ctx:
            return httpx.Response(200, json=[{"n_ctx": self.n_ctx}], request=request)
        if url.endswith("/models"):
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": self.model, "max_model_len": self.window}]},
                request=request,
            )
        return httpx.Response(404, request=request)

    def authorization_by_url(self):
        return {url: (kw.get("headers") or {}).get("Authorization") for url, kw in self.calls}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    _clear_caches()
    yield
    _clear_caches()


def _clear_caches():
    mc._context_cache.clear()
    mc._catalog_ctx_cache.clear()
    getattr(mc, "_refused_until", {}).clear()


class _Clock:
    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr(mc, "time", types.SimpleNamespace(monotonic=lambda: self.now), raising=False)


def _past_refusal_window(clock):
    clock.now += getattr(mc, "REFUSED_PROBE_RETRY_SECONDS", 60) + 1


# --- the reported bug ---------------------------------------------------------

def test_local_endpoint_reads_served_window_with_its_key(monkeypatch):
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base, kind="local"))
    server = _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800
    assert set(server.authorization_by_url().values()) == {f"Bearer {KEY}"}


@pytest.mark.parametrize("host", [
    "192.168.1.50:8092",
    "10.0.0.5:8092",
    "172.16.4.2:8092",
    "127.0.0.1:8092",
    "100.117.136.97:8092",
    "[fd7a:115c:a1e0::1]:8092",
    "gpu-box:8092",
    "gpu-box.tail1234.ts.net:8092",
    "gpu-box.lan:8092",
])
def test_keyed_private_server_on_auto_reads_served_window(monkeypatch, host):
    # Sending the key is not enough on its own: Auto + key + /v1 is "proxy",
    # which answers from the table before any probe runs.
    base = f"http://{host}/v1"
    _install_rows(monkeypatch, _row(base))
    _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800


def test_keyed_private_server_on_auto_may_serve_less_than_the_table(monkeypatch):
    # A self-hosted limit is the truth even below the table: the max() that
    # guards against low cloud defaults would hide it.
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base))
    _KeyedServer(monkeypatch, window=8192)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 8192


def test_keyed_llama_server_on_auto_reports_slots_context(monkeypatch):
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base))
    _KeyedServer(monkeypatch, n_ctx=32768)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 32768


def test_keyed_private_server_on_auto_restart_with_new_window_is_seen(monkeypatch):
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base))
    server = _KeyedServer(monkeypatch)
    url = f"{base}/chat/completions"

    assert mc.get_context_length(url, MODEL) == 204800
    server.window = 65536
    assert mc.get_context_length(url, MODEL) == 65536


def test_turn_probe_treats_keyed_private_auto_server_as_local(monkeypatch):
    # The turn resolver only reads /slots and re-probes after a restart for
    # local servers, so it has to agree with the lookup above.
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base))

    assert asyncio.run(cr._is_local(f"{base}/chat/completions")) is True


def test_private_gateway_on_auto_is_still_remote_for_request_shaping(monkeypatch):
    # is_local_endpoint gates llama.cpp-only request fields; flipping it would
    # send them to a private LiteLLM-style gateway. Only discovery changes.
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base))

    assert mc.is_local_endpoint(f"{base}/chat/completions") is False


def test_stored_key_is_decrypted_before_it_is_sent(monkeypatch):
    from core import database

    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    base = "http://192.168.1.50:8092/v1"
    db = factory()
    try:
        db.add(database.ModelEndpoint(id="ep", name="ninfer", base_url=base, api_key=KEY))
        db.commit()
        stored = db.execute(text("SELECT api_key FROM model_endpoints")).scalar()
    finally:
        db.close()
    assert stored != KEY
    monkeypatch.setattr(database, "SessionLocal", factory)
    _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800


def test_headers_come_from_build_headers_for_the_provider(monkeypatch):
    # Anthropic authenticates with x-api-key, not a bearer token.
    base = "https://api.anthropic.com"
    _install_rows(monkeypatch, _row(base))
    server = _KeyedServer(monkeypatch, key=None)

    mc.get_context_length(f"{base}/v1/messages", UNKNOWN_MODEL)

    headers = server.calls[-1][1]["headers"]
    assert headers["x-api-key"] == KEY
    assert headers["anthropic-version"]
    assert "Authorization" not in headers


# --- the key goes to its own endpoint and nowhere else -------------------------

@pytest.mark.parametrize("api_key", [None, ""])
def test_endpoint_without_a_key_passes_no_headers_argument(monkeypatch, api_key):
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base, kind="local", api_key=api_key))
    server = _KeyedServer(monkeypatch, key=None)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800
    assert server.calls
    assert all("headers" not in kwargs for _, kwargs in server.calls)


@pytest.mark.parametrize("probe_url", [
    "http://192.168.1.51:8092/v1/chat/completions",  # another host
    "http://192.168.1.50:8093/v1/chat/completions",  # another port
    "https://192.168.1.50:8092/v1/chat/completions",  # another scheme
    "http://192.168.1.50:8092/v1beta/chat/completions",  # only a string prefix
    "http://192.168.1.50:80921/v1/chat/completions",  # only a string prefix
])
def test_key_is_not_sent_to_a_url_outside_its_endpoint(monkeypatch, probe_url):
    _install_rows(monkeypatch, _row("http://192.168.1.50:8092/v1"))
    server = _KeyedServer(monkeypatch)

    mc.get_context_length(probe_url, MODEL)

    assert server.calls
    assert all("headers" not in kwargs for _, kwargs in server.calls)


def test_disabled_endpoint_key_is_not_used(monkeypatch):
    base = "http://192.168.1.50:8092/v1"
    row = _row(base)
    row.is_enabled = False
    _install_rows(monkeypatch, row)
    server = _KeyedServer(monkeypatch)

    mc.get_context_length(f"{base}/chat/completions", MODEL)

    assert all("headers" not in kwargs for _, kwargs in server.calls)


def test_most_specific_base_owns_the_key(monkeypatch):
    # Listed broad-first: table order must not hand team A's URL the broad key.
    broad = _row("http://192.168.1.50:8092/v1", kind="local", api_key="fake-broad-key")
    team = _row("http://192.168.1.50:8092/v1/team-a", kind="local", api_key=KEY)
    _install_rows(monkeypatch, broad, team)
    server = _KeyedServer(monkeypatch)

    mc.get_context_length("http://192.168.1.50:8092/v1/team-a/chat/completions", MODEL)

    assert server.authorization_by_url()["http://192.168.1.50:8092/v1/team-a/models"] == f"Bearer {KEY}"


def test_row_with_a_key_wins_among_rows_on_the_same_base(monkeypatch):
    # One row per owner: a keyless twin listed first must not cost the probe its key.
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base, kind="local", api_key=None), _row(base, kind="local"))
    _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800


def test_more_specific_keyless_endpoint_does_not_borrow_a_parent_key(monkeypatch):
    broad = _row("http://192.168.1.50:8092/v1", kind="local", api_key="fake-broad-key")
    team = _row("http://192.168.1.50:8092/v1/team-a", kind="local", api_key=None)
    _install_rows(monkeypatch, broad, team)
    server = _KeyedServer(monkeypatch)

    mc.get_context_length("http://192.168.1.50:8092/v1/team-a/chat/completions", MODEL)

    assert all("headers" not in kwargs for _, kwargs in server.calls)


@pytest.mark.parametrize("row_base,target", [
    ("http://models", "http://unrelated.example/v1/chat/completions"),
    ("http://models", "http://10.9.9.9:9000/v1/chat/completions"),
    ("http://completions", "http://unrelated.example/v1/chat/completions"),
    ("https://completions", "https://unrelated.example/v1/chat/completions"),
])
def test_row_whose_host_looks_like_a_suffix_covers_nothing_else(monkeypatch, row_base, target):
    # Normalizing strips the "/models" of "http://models" down to "http:", and
    # every http URL starts with that plus a slash.
    _install_rows(monkeypatch, _row(row_base))
    server = _KeyedServer(monkeypatch)

    assert mc._configured_endpoint_kind(target) is None
    mc.get_context_length(target, MODEL)

    assert all("headers" not in kwargs for _, kwargs in server.calls)


def test_host_named_like_a_suffix_still_owns_its_own_url(monkeypatch):
    base = "http://models:8000/v1"
    _install_rows(monkeypatch, _row(base))
    server = _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800
    assert server.authorization_by_url()[f"{base}/models"] == f"Bearer {KEY}"


def test_malformed_row_does_not_hide_the_other_rows(monkeypatch):
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row("http://[not-a-host"), _row(base))
    _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 204800


@pytest.mark.parametrize("base,root", [
    ("http://10.0.0.5:8000/v1", "http://10.0.0.5:8000"),
    ("http://10.0.0.5:8000/team-a/v1", "http://10.0.0.5:8000/team-a"),
])
def test_serving_probes_get_the_key_at_the_endpoint_root(monkeypatch, base, root):
    _install_rows(monkeypatch, _row(base, kind="local"))
    server = _KeyedServer(monkeypatch)

    mc.get_context_length(f"{base}/chat/completions", MODEL)

    auth = server.authorization_by_url()
    assert auth[f"{root}/slots"] == auth[f"{root}/props"] == f"Bearer {KEY}"


@pytest.mark.parametrize("base,target", [
    ("http://10.0.0.5:8000/v1beta/team-a", "http://10.0.0.5:8000/v1beta/team-a/chat/completions"),
    ("http://10.0.0.5:8000/team-a", "http://10.0.0.5:8000/team-a"),
])
def test_serving_probes_outside_the_endpoint_get_no_key(monkeypatch, base, target):
    # The serving root is cut from the URL's text and lands on the host root here,
    # which on a path-routed gateway may be another tenant. /models stays on the base.
    _install_rows(monkeypatch, _row(base, kind="local"))
    server = _KeyedServer(monkeypatch)

    mc.get_context_length(target, MODEL)

    auth = server.authorization_by_url()
    assert auth["http://10.0.0.5:8000/slots"] is None and auth["http://10.0.0.5:8000/props"] is None
    assert auth[f"{base}/models"] == f"Bearer {KEY}"


def test_pathless_endpoint_keeps_the_key_away_from_the_derived_serving_host(monkeypatch):
    # "http://host:8092".rsplit("/", 1)[0] is "http:/", so the serving probes
    # land on the bogus host "http://slots". The real /models probe keeps the key.
    base = "http://192.168.1.50:8092"
    _install_rows(monkeypatch, _row(base, kind="local"))
    server = _KeyedServer(monkeypatch)

    assert mc.get_context_length(base, MODEL) == 204800

    auth = server.authorization_by_url()
    assert auth["http://slots"] is None and auth["http://props"] is None
    assert auth[f"{base}/models"] == f"Bearer {KEY}"


def test_provider_auth_endpoint_static_key_is_skipped(monkeypatch):
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base, kind="local", provider_auth_id="auth-1"))
    server = _KeyedServer(monkeypatch)

    mc.get_context_length(f"{base}/chat/completions", MODEL)

    assert server.calls
    assert all("headers" not in kwargs for _, kwargs in server.calls)


@pytest.mark.parametrize("base,kind", [
    ("https://api.githubcopilot.com", "auto"),
    ("https://api.githubcopilot.com", "api"),
    ("https://copilot-api.corp.internal/v1", "auto"),
])
def test_copilot_is_untouched(monkeypatch, base, kind):
    _install_rows(monkeypatch, _row(base, kind=kind))
    server = _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", "gpt-4o") == 128000
    assert server.calls == []

    mc.get_context_length(f"{base}/chat/completions", UNKNOWN_MODEL)
    assert all("headers" not in kwargs for _, kwargs in server.calls)


# --- the cheap path stays cheap ------------------------------------------------

@pytest.mark.parametrize("base,kind", [
    ("http://192.168.1.50:8092/v1", "proxy"),
    ("http://192.168.1.50:8092/v1", "api"),
    ("https://openrouter.example.com/v1", "proxy"),
    ("https://llm.example.com/v1", "api"),
    ("https://llm.example.com/v1", "auto"),
    ("https://llm.example.com/openai", "auto"),
])
def test_known_model_on_explicit_or_public_endpoint_downloads_nothing(monkeypatch, base, kind):
    _install_rows(monkeypatch, _row(base, kind=kind))
    server = _KeyedServer(monkeypatch)

    assert mc.get_context_length(f"{base}/chat/completions", MODEL) == 131072
    assert server.calls == []


@pytest.mark.parametrize("broad_key", [KEY, None])
@pytest.mark.parametrize("broad_first", [True, False])
def test_explicit_proxy_row_keeps_its_cheap_path_under_an_auto_row(monkeypatch, broad_first, broad_key):
    broad = _row("http://192.168.1.50:8092/v1", api_key=broad_key)
    team = _row("http://192.168.1.50:8092/v1/team-a", kind="proxy")
    _install_rows(monkeypatch, *([broad, team] if broad_first else [team, broad]))
    server = _KeyedServer(monkeypatch, window=8192)

    url = "http://192.168.1.50:8092/v1/team-a/chat/completions"
    assert mc.get_context_length(url, MODEL) == 131072
    assert server.calls == []


@pytest.mark.parametrize("keyless_first", [True, False])
def test_keyed_auto_row_is_probed_under_a_keyless_auto_row(monkeypatch, keyless_first):
    keyless = _row("http://gpu.tail1234.ts.net:8092/v1", api_key=None)
    keyed = _row("http://gpu.tail1234.ts.net:8092/v1/team-a")
    _install_rows(monkeypatch, *([keyless, keyed] if keyless_first else [keyed, keyless]))
    _KeyedServer(monkeypatch, window=8192)

    url = "http://gpu.tail1234.ts.net:8092/v1/team-a/chat/completions"
    assert mc.get_context_length(url, MODEL) == 8192


def test_request_shaping_still_reads_the_first_matching_row(monkeypatch):
    # Only discovery follows the owning row: _configured_endpoint_kind keeps its
    # first-match result for the llama.cpp request-field decision.
    broad = _row("http://192.168.1.50:8092/v1", api_key=None)
    team = _row("http://192.168.1.50:8092/v1/team-a", kind="proxy")
    _install_rows(monkeypatch, broad, team)

    assert mc._configured_endpoint_kind("http://192.168.1.50:8092/v1/team-a/chat/completions") == "auto"


def test_keyless_named_host_on_auto_keeps_its_generic_probe(monkeypatch):
    base = "http://gpu-box:8092/v1"
    _install_rows(monkeypatch, _row(base, api_key=None))
    server = _KeyedServer(monkeypatch, key=None)

    mc.get_context_length(f"{base}/chat/completions", MODEL)

    assert [url for url, _ in server.calls] == [f"{base}/models"]


def test_proxy_catalog_is_read_with_the_key_and_fetched_once(monkeypatch):
    base = "https://llm.example.com/v1"
    _install_rows(monkeypatch, _row(base, kind="proxy"))
    server = _KeyedServer(monkeypatch, model=UNKNOWN_MODEL)
    url = f"{base}/chat/completions"

    assert mc.get_context_length(url, UNKNOWN_MODEL) == 204800
    assert mc.get_context_length(url, "another-unlisted-model") == mc.DEFAULT_CONTEXT
    assert len(server.calls) == 1


# --- a refused probe is not a fact about the server ----------------------------

def test_refused_catalog_is_retried_once_the_key_works(monkeypatch):
    base = "https://rotated.example.com/v1"
    _install_rows(monkeypatch, _row(base, kind="proxy", api_key="fake-stale-key"))
    _KeyedServer(monkeypatch, model=UNKNOWN_MODEL)
    clock = _Clock(monkeypatch)
    url = f"{base}/chat/completions"

    assert mc.get_context_length_known(url, UNKNOWN_MODEL) == (mc.DEFAULT_CONTEXT, False)
    assert url not in mc._catalog_ctx_cache

    _install_rows(monkeypatch, _row(base, kind="proxy"))
    _past_refusal_window(clock)
    assert mc.get_context_length_known(url, UNKNOWN_MODEL) == (204800, True)


def test_refused_probe_does_not_cache_the_table_value(monkeypatch):
    # Public + not /v1 keeps the generic path, whose non-default result is cached.
    base = "https://llm.example.com/gateway"
    _install_rows(monkeypatch, _row(base, api_key="fake-stale-key"))
    _KeyedServer(monkeypatch)
    clock = _Clock(monkeypatch)
    url = f"{base}/chat/completions"

    assert mc.get_context_length(url, MODEL) == 131072

    _install_rows(monkeypatch, _row(base))
    _past_refusal_window(clock)
    assert mc.get_context_length(url, MODEL) == 204800


@pytest.mark.parametrize("rows,url,model", [
    # The /setup row: a /v1/messages session URL normalizes past it, so no key is sent.
    ([_row("https://api.anthropic.com/v1")], "https://api.anthropic.com/v1/messages", "claude-sonnet-4-5"),
    ([], "https://llm.example.com/openai/v1/chat/completions", MODEL),
    ([_row("https://llm.example.com/v1", kind="api")], "https://llm.example.com/v1/chat/completions", UNKNOWN_MODEL),
    ([_row("https://llm.example.com/v1")], "https://llm.example.com/v1/chat/completions", UNKNOWN_MODEL),
    ([_row("https://llm.example.com/gateway")], "https://llm.example.com/gateway/chat/completions", UNKNOWN_MODEL),
])
def test_refused_probe_costs_one_request_per_window(monkeypatch, rows, url, model):
    # Lookups block the request path, and retrying right away can't change the
    # answer, so a /models that always refuses must not be asked on every lookup.
    _install_rows(monkeypatch, *rows)
    server = _KeyedServer(monkeypatch, key="fake-other-key")
    clock = _Clock(monkeypatch)

    first = mc.get_context_length_known(url, model)
    assert [mc.get_context_length_known(url, model) for _ in range(4)] == [first] * 4
    assert len(server.calls) == 1

    _past_refusal_window(clock)
    assert mc.get_context_length_known(url, model) == first
    assert len(server.calls) == 2


def test_refusal_window_is_shared_by_every_model_on_the_endpoint(monkeypatch):
    base = "https://llm.example.com/v1"
    _install_rows(monkeypatch, _row(base, kind="api"))
    server = _KeyedServer(monkeypatch, key="fake-other-key")
    url = f"{base}/chat/completions"

    assert mc.get_context_length_known(url, UNKNOWN_MODEL) == (mc.DEFAULT_CONTEXT, False)
    assert mc.get_context_length_known(url, "another-unlisted-model") == (mc.DEFAULT_CONTEXT, False)
    assert len(server.calls) == 1


def test_answered_probe_clears_the_refusal(monkeypatch):
    base = "https://llm.example.com/v1"
    _install_rows(monkeypatch, _row(base, kind="api"))
    server = _KeyedServer(monkeypatch, key="fake-other-key", model=UNKNOWN_MODEL)
    clock = _Clock(monkeypatch)
    url = f"{base}/chat/completions"

    mc.get_context_length(url, UNKNOWN_MODEL)
    server.key = KEY
    _past_refusal_window(clock)
    assert mc.get_context_length(url, UNKNOWN_MODEL) == 204800
    assert url not in mc._refused_until


def test_local_server_is_reprobed_even_after_a_refusal(monkeypatch):
    # A local lookup never served from a cache on dev; a refusal must not start now.
    base = "http://192.168.1.50:8092/v1"
    _install_rows(monkeypatch, _row(base, kind="local", api_key="fake-stale-key"))
    _KeyedServer(monkeypatch)
    url = f"{base}/chat/completions"

    assert mc.get_context_length(url, MODEL) == 131072

    _install_rows(monkeypatch, _row(base, kind="local"))
    assert mc.get_context_length(url, MODEL) == 204800


def test_answered_remote_probe_is_still_cached(monkeypatch):
    base = "https://llm.example.com/gateway"
    _install_rows(monkeypatch, _row(base))
    server = _KeyedServer(monkeypatch)
    url = f"{base}/chat/completions"

    assert mc.get_context_length(url, MODEL) == 204800
    assert mc.get_context_length(url, MODEL) == 204800
    assert len(server.calls) == 1
