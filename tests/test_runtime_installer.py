import contextlib
import json

import httpx
import pytest

from src import runtime_installer as rt

BODY = b"MZ" + bytes(range(256)) * 4  # stand-in installer payload
GOOD = ("Valid", "CN=Ollama Inc., O=Ollama Inc., L=Toronto, C=CA")


def _chain_handler(seen=None, bad_host=False):
    def handler(request):
        if seen is not None:
            seen.append(request)
        host = request.url.host
        if host == "ollama.com":
            target = "http://evil.example/x.exe" if bad_host else "https://github.com/a/b/OllamaSetup.exe"
            return httpx.Response(302, headers={"location": target})
        if host == "github.com":
            return httpx.Response(302, headers={"location": "https://release-assets.githubusercontent.com/f?sig=1"})
        rng = request.headers.get("range", "bytes=0-")
        start, _, end = rng.removeprefix("bytes=").partition("-")
        start = int(start)
        end = int(end) if end else len(BODY) - 1
        return httpx.Response(
            206, content=BODY[start:end + 1],
            headers={"content-range": f"bytes {start}-{end}/{len(BODY)}"},
        )
    return handler


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_is_windows", lambda: True)
    monkeypatch.setattr(rt, "find_ollama", lambda: None)
    monkeypatch.setattr(rt, "_cache_dir", lambda: tmp_path)
    return tmp_path


def _fake_net(monkeypatch, handler):
    monkeypatch.setattr(
        rt, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    )


def test_offer_none_off_windows(monkeypatch):
    monkeypatch.setattr(rt, "_is_windows", lambda: False)
    assert rt.ollama_install_offer() is None


def test_offer_reports_real_size(win, monkeypatch):
    _fake_net(monkeypatch, _chain_handler())
    offer = rt.ollama_install_offer()
    assert offer["size_bytes"] == len(BODY)
    assert offer["license"] == "MIT (Ollama)"


def test_disallowed_redirect_host_rejected(win, monkeypatch):
    seen = []
    _fake_net(monkeypatch, _chain_handler(seen, bad_host=True))
    with pytest.raises(rt.RuntimeInstallError):
        rt.ollama_install_offer()
    assert all(r.url.host != "evil.example" for r in seen)


@pytest.mark.parametrize("sig", [("HashMismatch", "O=Ollama Inc."), ("NotSigned", ""),
                                 ("Valid", "CN=Evil, O=Evil Corp"), ("Valid", "CN=Ollama Inc.x, O=Not Ollama Inc.")])
def test_bad_signature_deletes_and_never_runs(win, monkeypatch, sig):
    _fake_net(monkeypatch, _chain_handler())
    monkeypatch.setattr(rt, "_signature_info", lambda p: sig)
    ran = []
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(rt.RuntimeInstallError):
        rt.install_ollama(lambda m: None)
    assert ran == []
    assert list(win.iterdir()) == []


def test_good_signature_runs_silent_and_cleans_up(win, monkeypatch):
    _fake_net(monkeypatch, _chain_handler())
    monkeypatch.setattr(rt, "_signature_info", lambda p: GOOD)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        monkeypatch.setattr(rt, "find_ollama", lambda: "C:/fake/ollama.exe")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(rt.subprocess, "run", fake_run)
    assert rt.install_ollama(lambda m: None) == "C:/fake/ollama.exe"
    assert calls[0][1:] == ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
    assert list(win.iterdir()) == []


def test_download_resumes_with_range_and_appends(win, monkeypatch):
    seen = []
    _fake_net(monkeypatch, _chain_handler(seen))
    part = win / "x.part"
    part.write_bytes(BODY[:100])
    with rt._client() as c:
        rt._download(c, "https://release-assets.githubusercontent.com/f", len(BODY), part, lambda m: None)
    assert seen[-1].headers["range"] == "bytes=100-"
    assert part.read_bytes() == BODY


class _Stream:
    def __init__(self, lines, status=200, fail=False):
        self.lines, self.status_code, self.fail = lines, status, fail

    def iter_lines(self):
        yield from self.lines
        if self.fail:
            raise httpx.ReadError("boom")


def _patch_stream(monkeypatch, stream):
    @contextlib.contextmanager
    def fake(*a, **k):
        yield stream
    monkeypatch.setattr(rt.httpx, "stream", fake)


def test_pull_progress_is_monotonic_across_layers(monkeypatch):
    msgs = [
        {"status": "pulling manifest"},
        {"digest": "a", "total": 100, "completed": 10},
        {"digest": "a", "total": 100, "completed": 60},
        {"digest": "b", "total": 50, "completed": 0},
        {"digest": "a", "total": 100, "completed": 100},
        {"digest": "b", "total": 50, "completed": 50},
        {"status": "success"},
    ]
    _patch_stream(monkeypatch, _Stream([json.dumps(m) for m in msgs]))
    got = []
    rt.pull_model("http://x", "m", lambda d, t: got.append((d, t)))
    done = [d for d, _ in got]
    assert done == sorted(done) and got[-1] == (150, 150)
    assert all(d <= t for d, t in got)


def test_pull_network_failure_is_friendly(monkeypatch):
    _patch_stream(monkeypatch, _Stream([json.dumps({"digest": "a", "total": 9, "completed": 1})], fail=True))
    with pytest.raises(rt.RuntimeInstallError, match="Try Again"):
        rt.pull_model("http://x", "m", lambda d, t: None)


def test_ensure_running_does_not_spawn_when_up(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(rt, "_answers", lambda base: True)
    spawned = []
    monkeypatch.setattr(rt.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    assert rt.ensure_ollama_running("ollama") == "http://127.0.0.1:11434"
    assert spawned == []


@pytest.mark.parametrize("host,url", [("0.0.0.0:9999", "http://127.0.0.1:9999"), ("myhost", "http://myhost:11434"),
                                      ("https://h:1", "https://h:1")])
def test_base_url_honors_ollama_host(monkeypatch, host, url):
    monkeypatch.setenv("OLLAMA_HOST", host)
    assert rt._base_url() == url


def test_recommend_model_uses_manifest_size_and_falls_back(monkeypatch):
    manifest = {"config": {"size": 10}, "layers": [{"size": 100}, {"size": 5}]}
    monkeypatch.setattr(rt.httpx, "get", lambda *a, **k: httpx.Response(200, json=manifest, request=httpx.Request("GET", "http://x")))
    assert rt.recommend_model(16, 0) == {"model": "qwen3:4b", "size_bytes": 115, "license": "Apache 2.0"}
    assert rt.recommend_model(8, 12)["model"] == "qwen3:8b"

    def down(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(rt.httpx, "get", down)
    small = rt.recommend_model(4, 0)
    assert small["model"] == "qwen3:1.7b" and small["size_bytes"] > 0


# ---- agent readiness ------------------------------------------------------------

ROOT = "http://127.0.0.1:11434"


def _resp(status=200, data=None, text=None):
    req = httpx.Request("GET", "http://x")
    if text is not None:
        return httpx.Response(status, text=text, request=req)
    return httpx.Response(status, json=data, request=req)


def _route(monkeypatch, routes, sent=None):
    """Fake httpx.get/post: routes maps (method, path) to a response or an exception."""
    def call(method):
        def f(url, **kw):
            if sent is not None:
                sent.append((method, url, kw.get("json")))
            out = routes[(method, url.removeprefix(ROOT))]
            if isinstance(out, Exception):
                raise out
            return out
        return f
    monkeypatch.setattr(rt.httpx, "get", call("GET"))
    monkeypatch.setattr(rt.httpx, "post", call("POST"))


def test_ollama_root_good_answer(monkeypatch):
    _route(monkeypatch, {("GET", "/api/version"): _resp(data={"version": "0.40.15"})})
    assert rt.ollama_root(ROOT + "/v1/") == ROOT


@pytest.mark.parametrize("url,answer", [
    (ROOT, _resp(data={"version": "1"})),
    (ROOT + "/v1", _resp(text="<html>")),
    (ROOT + "/v1", httpx.ConnectError("down")),
])
def test_ollama_root_other_cases_give_none(monkeypatch, url, answer):
    _route(monkeypatch, {("GET", "/api/version"): answer})
    assert rt.ollama_root(url) is None


def test_model_details_parses_tools_and_context(monkeypatch):
    show = {"capabilities": ["tools", "thinking"], "parameters": "temperature   0.7\nnum_ctx        4096\nstop  \"x\""}
    _route(monkeypatch, {("POST", "/api/show"): _resp(data=show)})
    assert rt.model_details(ROOT, "m") == {"tools": True, "num_ctx": 4096}


def test_model_details_without_fields(monkeypatch):
    _route(monkeypatch, {("POST", "/api/show"): _resp(data={})})
    assert rt.model_details(ROOT, "m") == {"tools": False, "num_ctx": None}


def test_loaded_context_found_and_missing(monkeypatch):
    routes = {("POST", "/api/generate"): _resp(data={"done_reason": "load"}),
              ("GET", "/api/ps"): _resp(data={"models": [{"name": "m:latest", "model": "m:latest", "context_length": 4096}]})}
    _route(monkeypatch, routes)
    assert rt.loaded_context(ROOT, "m") == 4096
    routes[("GET", "/api/ps")] = _resp(data={"models": [{"name": "m", "model": "m"}]})
    assert rt.loaded_context(ROOT, "m") is None


def test_agent_context_size():
    assert rt.agent_context_size(16, 0) == 16384
    assert rt.agent_context_size(4, 12) == 16384
    assert rt.agent_context_size(8, 4) == 8192


def test_create_context_variant_sends_exact_request(monkeypatch):
    sent = []
    _route(monkeypatch, {("GET", "/api/tags"): _resp(data={"models": [{"name": "m"}]}),
                         ("POST", "/api/create"): _resp(data={"status": "success"})}, sent)
    assert rt.create_context_variant(ROOT, "m", 16384) == "m-ctx16k"
    assert sent[-1] == ("POST", ROOT + "/api/create",
                        {"model": "m-ctx16k", "from": "m", "parameters": {"num_ctx": 16384}, "stream": False})


def test_create_context_variant_never_overwrites(monkeypatch):
    sent = []
    _route(monkeypatch, {("GET", "/api/tags"): _resp(data={"models": [{"name": "m-ctx8k"}]})}, sent)
    with pytest.raises(rt.RuntimeInstallError):
        rt.create_context_variant(ROOT, "m", 8192)
    assert all(m == "GET" for m, _, _ in sent)


def test_create_context_variant_failure_status_raises(monkeypatch):
    _route(monkeypatch, {("GET", "/api/tags"): _resp(data={"models": []}),
                         ("POST", "/api/create"): _resp(status=500, data={"error": "no"})})
    with pytest.raises(rt.RuntimeInstallError):
        rt.create_context_variant(ROOT, "m", 8192)


@pytest.mark.parametrize("answer,expected", [
    (_resp(data={"choices": [{"message": {"tool_calls": [{"function": {"name": "read_file"}}]}}]}), True),
    (_resp(data={"choices": [{"message": {"content": "hello"}}]}), False),
    (_resp(status=400, data={"error": "no tools"}), False),
    (httpx.ConnectError("down"), False),
])
def test_probe_tool_call(monkeypatch, answer, expected):
    _route(monkeypatch, {("POST", "/v1/chat/completions"): answer})
    assert rt.probe_tool_call(ROOT, "m") is expected
