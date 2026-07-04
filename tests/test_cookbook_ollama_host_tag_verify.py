"""Regression for PR #4535 follow-up — a local `ollama serve` must verify the
selected model against the target Ollama daemon before pinning it, since a local
serve never imports an HF-GGUF repo or pulls a missing tag.

Covers the pure matcher (`_ollama_tag_served`, Ollama's implicit-`:latest`,
case-insensitive semantics), the native `/api/tags` probe (`_probe_ollama_tags`
— unfiltered, so it keeps embedding tags the chat filter would drop), and the
three route-level outcomes: container hard-reject, native reject when the daemon
is reachable but lacks the tag, and native defer-pin when the daemon is
unreachable (the launch may itself start it; the post-launch re-probe decides
what the endpoint serves).
"""

import pytest

# The module imports fastapi/sqlalchemy at top level; skip cleanly where the
# serving stack isn't installed (pure-JS/test-tooling environments) so this file
# never blocks collection.
cb = pytest.importorskip("routes.cookbook_routes")
_ollama_tag_served = cb._ollama_tag_served

# Importorskip above proves fastapi/sqlalchemy are importable, so the harness
# imports below are safe here.
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
from routes.cookbook_helpers import ServeRequest  # noqa: E402


def _model_serve_endpoint():
    router = cb.setup_cookbook_routes()
    for route in router.routes:
        if route.path == "/api/model/serve" and "POST" in route.methods:
            return route.endpoint
    raise AssertionError("POST /api/model/serve route not found")


def _admin_request() -> Request:
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/model/serve",
        "headers": [],
        "state": {},
    })
    request.state.current_user = "admin"
    return request


def _force_native_local(monkeypatch):
    """Pin the serve handler onto the native-local `ollama serve` path: no
    remote, not inside Docker (so `_serve_in_container` is False)."""
    _real_exists = cb.os.path.exists
    monkeypatch.setattr(
        cb.os.path, "exists",
        lambda p: False if p == "/.dockerenv" else _real_exists(p),
    )


def test_probe_ollama_tags_unfiltered(monkeypatch):
    """`_probe_ollama_tags` must return EVERY served tag (no chat filter), hit
    the native `/api/tags`, and strip an accidental `/v1` suffix from the base.

    Uses `bge-m3`, a real embedding tag the chat filter genuinely drops — asserted
    below — so this test actually exercises the gap the native probe closes (the
    chat-filtered `/v1/models` path would omit it and the gate would false-reject)."""
    mr = pytest.importorskip("routes.model_routes")

    # Guard the premise: the chat filter really would drop this tag, so the
    # unfiltered native probe is load-bearing and not just incidental.
    assert mr._is_chat_model("bge-m3:latest") is False

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [
                {"name": "qwen3:latest"},
                {"name": "bge-m3:latest"},  # embedding: chat filter drops it (see assert above)
            ]}

    def _fake_get(url, **kwargs):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(mr.httpx, "get", _fake_get)
    served = mr._probe_ollama_tags("http://host.docker.internal:11434/v1", timeout=5)
    assert captured["url"] == "http://host.docker.internal:11434/api/tags"
    assert served == ["qwen3:latest", "bge-m3:latest"]


def test_probe_ollama_tags_returns_empty_on_error(monkeypatch):
    """Unreachable host → `[]`, so the route reports 'could not reach' guidance."""
    mr = pytest.importorskip("routes.model_routes")

    def _boom(url, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mr.httpx, "get", _boom)
    assert mr._probe_ollama_tags("http://host.docker.internal:11434") == []


@pytest.mark.parametrize("repo_id, served", [
    ("qwen3", ["qwen3:latest", "llama3.2:8b"]),          # implicit :latest match
    ("qwen3:latest", ["qwen3:latest"]),                  # exact
    ("qwen3:8b", ["qwen3:8b", "qwen3:latest"]),          # specific tag present
    ("library/qwen3:8b", ["library/qwen3:8b"]),          # namespaced tag (was wrongly rejected)
    ("Qwen3", ["qwen3:latest"]),                         # case-insensitive
    ("llama3.2", ["llama3.2"]),                          # both implicit :latest
    ("bge-m3", ["bge-m3:latest"]),                       # embedding tag dropped by chat filter
])
def test_served_tags_match(repo_id, served):
    assert _ollama_tag_served(repo_id, served) is True


@pytest.mark.parametrize("repo_id, served", [
    ("qwen3:8b", ["qwen3:latest"]),                      # only :latest installed, not :8b
    ("mistral", ["qwen3:latest"]),                       # different model
    ("TheBloke/Foo-GGUF", ["qwen3:latest"]),             # HF-GGUF repo, never imported
    ("qwen3", []),                                        # host unreachable / nothing served
    ("qwen3", None),                                      # defensive: None list
])
def test_unserved_tags_rejected(repo_id, served):
    assert _ollama_tag_served(repo_id, served) is False


# ── Route-level: native local `ollama serve` verification ──────────────────
#
# The pure matcher above is host-agnostic; these drive the actual serve handler
# on the native-local path (not remote, not in Docker) to lock in the three
# outcomes the follow-up review asked for.


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_id", [
    "mistral",            # bare tag the local daemon hasn't pulled
    "TheBloke/Foo-GGUF",  # HF-GGUF repo `ollama serve` can't import locally
])
async def test_native_local_reachable_missing_tag_rejected(monkeypatch, tmp_path, repo_id):
    """Native local, daemon reachable, tag genuinely absent → 400 with pull
    guidance (never a phantom pin), for both a missing bare tag and an HF-GGUF
    repo `ollama serve` cannot import."""
    mr = pytest.importorskip("routes.model_routes")
    _force_native_local(monkeypatch)
    monkeypatch.setattr(cb, "require_admin", lambda request: None)
    monkeypatch.setattr(cb, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cb, "load_stored_hf_token", lambda **kwargs: "")
    # Daemon reachable but only serves an unrelated tag.
    monkeypatch.setattr(mr, "_probe_ollama_tags", lambda *a, **k: ["qwen3:latest"])

    with pytest.raises(HTTPException) as exc:
        await _model_serve_endpoint()(
            _admin_request(),
            ServeRequest(repo_id=repo_id, cmd="ollama serve"),
        )
    assert exc.value.status_code == 400
    assert "not available in your local Ollama" in exc.value.detail
    assert f"ollama pull {repo_id}" in exc.value.detail


@pytest.mark.asyncio
async def test_native_local_served_tag_is_pinned(monkeypatch, tmp_path):
    """Native local, daemon reachable, tag served → launch proceeds and the tag
    is pinned as an available Ollama model."""
    mr = pytest.importorskip("routes.model_routes")
    from core.database import SessionLocal, ModelEndpoint
    import json

    _prepare_native_local_launch(monkeypatch, tmp_path)
    monkeypatch.setattr(mr, "_probe_ollama_tags", lambda *a, **k: ["qwen3:latest"])

    resp = await _model_serve_endpoint()(
        _admin_request(),
        # Pin the port in the command so the base_url is deterministic and the
        # port-scan rewrite (a nested helper we can't monkeypatch) is skipped.
        ServeRequest(repo_id="qwen3", cmd="OLLAMA_HOST=127.0.0.1:15561 ollama serve"),
    )

    assert resp["ok"] is True
    assert resp["endpoint_id"]
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.base_url == "http://localhost:15561/v1"
        ).first()
        assert ep is not None
        assert json.loads(ep.pinned_models) == ["qwen3"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_native_local_unreachable_daemon_defers_pin(monkeypatch, tmp_path):
    """Native local, daemon not up yet → launch proceeds but the unverified tag
    is NOT pinned; the post-launch re-probe decides what the endpoint serves."""
    mr = pytest.importorskip("routes.model_routes")
    from core.database import SessionLocal, ModelEndpoint

    _prepare_native_local_launch(monkeypatch, tmp_path)
    # Empty probe = daemon unreachable (this serve may be what starts it).
    monkeypatch.setattr(mr, "_probe_ollama_tags", lambda *a, **k: [])

    resp = await _model_serve_endpoint()(
        _admin_request(),
        ServeRequest(repo_id="qwen3", cmd="OLLAMA_HOST=127.0.0.1:15562 ollama serve"),
    )

    assert resp["ok"] is True
    assert resp["endpoint_id"]
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.base_url == "http://localhost:15562/v1"
        ).first()
        assert ep is not None
        # No phantom pin for the unverified tag.
        assert ep.pinned_models is None
        assert ep.cached_models is None
    finally:
        db.close()


def _prepare_native_local_launch(monkeypatch, tmp_path):
    """Mock everything the native-local serve handler touches after the gate so
    the launch + auto-register runs without a real tmux/daemon/network."""
    _force_native_local(monkeypatch)
    monkeypatch.setattr(cb, "require_admin", lambda request: None)
    monkeypatch.setattr(cb, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(cb, "load_stored_hf_token", lambda **kwargs: "")

    async def _binary_available(binary, remote, ssh_port, **kwargs):
        return True

    monkeypatch.setattr(cb, "_binary_available", _binary_available)

    class _Proc:
        returncode = 0

        async def wait(self):
            return None

    async def _launch(setup_cmd, **kwargs):
        return _Proc()

    monkeypatch.setattr(cb.asyncio, "create_subprocess_shell", _launch)

    # The post-launch crash watchdog is a nested coroutine we can't patch by
    # name; stop it from ever being scheduled (and close the coro so pytest
    # doesn't warn about an un-awaited coroutine).
    def _no_task(coro, *a, **k):
        try:
            coro.close()
        except AttributeError:
            pass
        return None

    monkeypatch.setattr(cb.asyncio, "create_task", _no_task)
    # Re-probe after auto-register: no server actually listening → empty.
    mr = pytest.importorskip("routes.model_routes")
    monkeypatch.setattr(mr, "_probe_endpoint", lambda *a, **k: [])

