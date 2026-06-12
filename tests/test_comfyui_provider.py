"""Tests for the ComfyUI provider: connection probe (S3) and text-to-image
generation queue/poll/retrieve (S4B).

HTTP is mocked by monkeypatching ``httpx.get`` / ``httpx.post`` at the provider
module path, matching the repo convention (see tests/test_lmstudio_discovery.py).
"""

import httpx

from services.media import comfyui
from services.media.comfyui import ComfyUIProvider
from src import media_registry


ENDPOINT = "http://localhost:8188"
# A fake checkpoint name (a ComfyUI-side model identifier, not a local path).
# The bundled workflow carries a %checkpoint% placeholder, so generation tests
# that use it must supply a checkpoint to clear the pre-flight checkpoint gate.
CKPT = "test-ckpt.safetensors"


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, raise_json=False):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self._raise = raise_json

    def json(self):
        if self._raise:
            raise ValueError("response was not valid JSON")
        return self._json


def _install_router(monkeypatch, router):
    """Install a fake httpx.get that dispatches on URL via ``router(url)``."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return router(url)

    monkeypatch.setattr(comfyui.httpx, "get", fake_get)
    return calls


# 1. Reachable ComfyUI via /system_stats ------------------------------------

def test_probe_online_via_system_stats(monkeypatch):
    def router(url):
        assert url.endswith("/system_stats")
        return _FakeResp(200, {"system": {"comfyui_version": "0.3.0"}, "devices": []})

    calls = _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    assert result["ok"] is True
    assert result["available"] is True
    assert result["status"] == "online"
    assert result["provider"] == "comfyui"
    assert result["endpoint"] == ENDPOINT
    assert result["via"] == "/system_stats"
    assert "0.3.0" in (result["detail"] or "")
    assert len(calls) == 1  # no fallback needed


# 2. Fallback success via /object_info --------------------------------------

def test_probe_falls_back_to_object_info_on_http_error(monkeypatch):
    def router(url):
        if url.endswith("/system_stats"):
            return _FakeResp(500, {})  # http error → triggers fallback
        return _FakeResp(200, {"KSampler": {"input": {}}})

    calls = _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    assert result["ok"] is True
    assert result["status"] == "online"
    assert result["via"] == "/object_info"
    assert [c.rsplit("/", 1)[-1] for c in calls] == ["system_stats", "object_info"]


def test_probe_falls_back_when_system_stats_malformed(monkeypatch):
    def router(url):
        if url.endswith("/system_stats"):
            return _FakeResp(200, raise_json=True)  # malformed → fallback
        return _FakeResp(200, {"KSampler": {}})

    calls = _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    assert result["ok"] is True
    assert result["via"] == "/object_info"
    assert len(calls) == 2


# 3. Unreachable / offline endpoint -----------------------------------------

def test_probe_unreachable_no_fallback(monkeypatch):
    def router(url):
        raise httpx.ConnectError("connection refused")

    calls = _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    assert result["ok"] is False
    assert result["available"] is False
    assert result["status"] == "unreachable"
    # F1: agent-visible text must not leak the endpoint URL or the raw error.
    assert ENDPOINT not in result["message"]
    assert "connection refused" not in (result["detail"] or "")
    assert result["detail"] == "ConnectError"  # leak-safe (type name only)
    # Network errors are terminal — must NOT retry the fallback path.
    assert len(calls) == 1


def test_probe_timeout_is_unreachable(monkeypatch):
    def router(url):
        raise httpx.ConnectTimeout("timed out")

    _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()
    assert result["status"] == "unreachable"


# 4. Malformed response (both paths) ----------------------------------------

def test_probe_malformed_both_paths_is_unavailable(monkeypatch):
    def router(url):
        return _FakeResp(200, raise_json=True)

    calls = _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert len(calls) == 2  # tried primary then fallback
    assert "/system_stats" in (result["detail"] or "")


def test_probe_non_dict_json_is_malformed(monkeypatch):
    def router(url):
        return _FakeResp(200, json_data=["not", "a", "dict"])

    _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()
    assert result["status"] == "unavailable"


# Additional coverage --------------------------------------------------------

def test_probe_not_configured_when_endpoint_empty(monkeypatch):
    # httpx.get must never be called when there is no endpoint.
    def boom(url, timeout=None):
        raise AssertionError("network call made without an endpoint")

    monkeypatch.setattr(comfyui.httpx, "get", boom)
    result = ComfyUIProvider("").probe()

    assert result["ok"] is False
    assert result["status"] == "not_configured"
    assert result["checked"][0]["provider"] == "comfyui"


def test_probe_auth_error_no_fallback(monkeypatch):
    def router(url):
        return _FakeResp(401, {})

    calls = _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    assert result["status"] == "auth_error"
    assert result["ok"] is False
    assert len(calls) == 1  # auth is terminal, no fallback


def test_from_settings_reads_endpoint(monkeypatch):
    provider = ComfyUIProvider.from_settings(
        settings={"comfyui_endpoint_url": "http://host:8188"}
    )
    assert provider.endpoint_url == "http://host:8188"


def test_module_probe_helper_prefers_explicit_url(monkeypatch):
    def router(url):
        return _FakeResp(200, {"system": {}})

    _install_router(monkeypatch, router)
    # Use a loopback URL so the local-by-default guard allows the probe.
    result = comfyui.probe("http://127.0.0.1:8188")
    assert result["endpoint"] == "http://127.0.0.1:8188"
    assert result["status"] == "online"


# Degraded-state shape compatibility ----------------------------------------

def test_probe_result_renders_with_media_registry_formatter(monkeypatch):
    def router(url):
        raise httpx.ConnectError("refused")

    _install_router(monkeypatch, router)
    result = ComfyUIProvider(ENDPOINT).probe()

    # Same keys as the shared degraded-state shape → reusable rendering.
    for key in ("ok", "available", "status", "message", "checked", "next_steps", "detail"):
        assert key in result
    text = media_registry.format_degraded_message(result)
    assert "Checked:" in text
    assert "- comfyui:" in text


# S4B guardrail: generation exists, but no video surface --------------------

def test_provider_exposes_generation_but_not_video():
    assert hasattr(ComfyUIProvider, "generate")
    assert hasattr(ComfyUIProvider, "probe")
    for forbidden in ("generate_video", "video"):
        assert not hasattr(ComfyUIProvider, forbidden), (
            f"S4B is image-only; {forbidden!r} must not exist yet"
        )


# S4B generation tests ------------------------------------------------------

class _FakeBytesResp:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "image/png"}


def _history_with_image(prompt_id, filename="out.png", subfolder="", type_="output"):
    return {
        prompt_id: {
            "outputs": {
                "9": {"images": [{"filename": filename, "subfolder": subfolder, "type": type_}]}
            },
            "status": {"completed": True},
        }
    }


def _install_generation_router(monkeypatch, *, post, get):
    """Install fake httpx.post/get + a no-op sleep; record calls."""
    posts = []
    gets = []

    def fake_post(url, json=None, timeout=None):
        posts.append({"url": url, "json": json})
        return post(url, json)

    def fake_get(url, params=None, timeout=None):
        gets.append({"url": url, "params": params})
        return get(url, params)

    monkeypatch.setattr(comfyui.httpx, "post", fake_post)
    monkeypatch.setattr(comfyui.httpx, "get", fake_get)
    monkeypatch.setattr(comfyui.time, "sleep", lambda *_a, **_k: None)
    return posts, gets


def test_generate_happy_path_queue_poll_view(monkeypatch):
    prompt_id = "pid-123"
    png = b"\x89PNG\r\n\x1a\nFAKE"

    def post(url, body):
        assert url.endswith("/prompt")
        return _FakeResp(200, {"prompt_id": prompt_id})

    def get(url, params):
        if "/history/" in url:
            assert url.endswith(f"/history/{prompt_id}")
            return _FakeResp(200, _history_with_image(prompt_id))
        assert url.endswith("/view")
        assert params == {"filename": "out.png", "subfolder": "", "type": "output"}
        return _FakeBytesResp(200, png)

    posts, gets = _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(
        prompt="a cat", width=512, height=512, seed=7, checkpoint=CKPT,
    )

    assert result["ok"] is True
    assert result["status"] == "generated"
    assert result["provider"] == "comfyui"
    assert result["image_bytes"] == png
    assert result["content_type"] == "image/png"
    assert result["prompt_id"] == prompt_id
    # POST /prompt, then GET /history, then GET /view were each exercised.
    assert len(posts) == 1
    assert any("/history/" in g["url"] for g in gets)
    assert any(g["url"].endswith("/view") for g in gets)


def test_generate_polls_until_output_ready(monkeypatch):
    prompt_id = "pid-poll"
    png = b"IMG"
    state = {"polls": 0}

    def post(url, body):
        return _FakeResp(200, {"prompt_id": prompt_id})

    def get(url, params):
        if "/history/" in url:
            state["polls"] += 1
            if state["polls"] < 3:
                return _FakeResp(200, {})  # not ready yet
            return _FakeResp(200, _history_with_image(prompt_id))
        return _FakeBytesResp(200, png)

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(prompt="x", seed=1, poll_interval=0, checkpoint=CKPT)

    assert result["ok"] is True
    assert state["polls"] >= 3


def test_generate_substitutes_only_known_fields(monkeypatch):
    """Workflow substitution touches placeholder inputs only — nothing else."""
    workflow = {
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": comfyui.PH_WIDTH, "height": comfyui.PH_HEIGHT, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": comfyui.PH_PROMPT}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": comfyui.PH_SEED, "steps": 20, "sampler_name": "euler"}},
        "meta": {"class_type": "Note", "inputs": {"text": "ignore me; do not change"}},
    }
    captured = {}

    def post(url, body):
        captured["wf"] = body["prompt"]
        return _FakeResp(200, {"prompt_id": "p"})

    def get(url, params):
        if "/history/" in url:
            return _FakeResp(200, _history_with_image("p"))
        return _FakeBytesResp(200, b"IMG")

    _install_generation_router(monkeypatch, post=post, get=get)
    ComfyUIProvider(ENDPOINT).generate(
        prompt="a fox", width=768, height=1024, seed=42, workflow=workflow,
    )

    wf = captured["wf"]
    assert wf["6"]["inputs"]["text"] == "a fox"
    assert wf["5"]["inputs"]["width"] == 768
    assert wf["5"]["inputs"]["height"] == 1024
    assert wf["3"]["inputs"]["seed"] == 42
    # Untouched fields stay exactly as authored.
    assert wf["3"]["inputs"]["steps"] == 20
    assert wf["3"]["inputs"]["sampler_name"] == "euler"
    assert wf["5"]["inputs"]["batch_size"] == 1
    assert wf["meta"]["inputs"]["text"] == "ignore me; do not change"
    # Original template object is not mutated in place.
    assert workflow["6"]["inputs"]["text"] == comfyui.PH_PROMPT


def test_generate_unreachable_endpoint(monkeypatch):
    def post(url, body):
        raise httpx.ConnectError("connection refused")

    def get(url, params):
        raise AssertionError("should not reach GET")

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(prompt="x", seed=1, checkpoint=CKPT)

    assert result["ok"] is False
    assert result["status"] == "unreachable"


def test_generate_queue_http_error_is_preserved_without_leaks(monkeypatch):
    def post(url, body):
        return _FakeResp(500, {"error": "boom"})

    def get(url, params):
        raise AssertionError("should not poll on queue failure")

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(prompt="x", seed=1, checkpoint=CKPT)

    assert result["ok"] is False
    assert result["status"] == "generation_failed"
    assert "HTTP 500" in (result.get("detail") or "")
    # No local filesystem paths leak in the rendered message.
    text = media_registry.format_degraded_message(result)
    assert "/Users/" not in text
    assert ".json" not in text


def test_generate_times_out_when_history_never_ready(monkeypatch):
    def post(url, body):
        return _FakeResp(200, {"prompt_id": "pid"})

    def get(url, params):
        if "/history/" in url:
            return _FakeResp(200, {})  # never ready
        raise AssertionError("should not fetch /view on timeout")

    _install_generation_router(monkeypatch, post=post, get=get)
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(comfyui.time, "monotonic", lambda: next(ticks))
    result = ComfyUIProvider(ENDPOINT).generate(
        prompt="x", seed=1, timeout=30, poll_interval=0, checkpoint=CKPT,
    )

    assert result["ok"] is False
    assert result["status"] == "timeout"


def test_coerce_generation_timeout_clamps_and_defaults():
    assert comfyui.coerce_generation_timeout(None) == comfyui.DEFAULT_GENERATE_TIMEOUT
    assert comfyui.coerce_generation_timeout(300) == 300.0
    assert comfyui.coerce_generation_timeout(10) == comfyui.MIN_GENERATE_TIMEOUT
    assert comfyui.coerce_generation_timeout(2000) == comfyui.MAX_GENERATE_TIMEOUT
    assert comfyui.coerce_generation_timeout("not-a-number") == comfyui.DEFAULT_GENERATE_TIMEOUT


def test_default_generate_timeout_is_slower_local_default():
    assert comfyui.DEFAULT_GENERATE_TIMEOUT == 300.0


def test_timeout_message_is_safe(monkeypatch):
    def post(url, body):
        return _FakeResp(200, {"prompt_id": "pid"})

    def get(url, params):
        if "/history/" in url:
            return _FakeResp(200, {})
        raise AssertionError("should not fetch /view on timeout")

    _install_generation_router(monkeypatch, post=post, get=get)
    ticks = iter([0.0, 500.0])
    monkeypatch.setattr(comfyui.time, "monotonic", lambda: next(ticks))
    result = ComfyUIProvider("http://10.9.9.9:8188").generate(
        prompt="x", seed=1, timeout=120, poll_interval=0, checkpoint=CKPT,
    )
    text = media_registry.format_degraded_message(result)
    assert result["status"] == "timeout"
    assert "10.9.9.9" not in text
    assert "8188" not in text
    assert "120" in text


def test_generate_no_image_in_output(monkeypatch):
    def post(url, body):
        return _FakeResp(200, {"prompt_id": "pid"})

    def get(url, params):
        if "/history/" in url:
            return _FakeResp(200, {"pid": {"outputs": {"9": {"images": []}}}})
        raise AssertionError("should not fetch /view without an image")

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(prompt="x", seed=1, poll_interval=0, checkpoint=CKPT)

    assert result["ok"] is False
    assert result["status"] == "generation_failed"


def test_generate_not_configured_without_endpoint(monkeypatch):
    result = ComfyUIProvider("").generate(prompt="x", seed=1)
    assert result["ok"] is False
    assert result["status"] == "not_configured"


def test_generate_workflow_missing(monkeypatch):
    # An empty/invalid explicit workflow yields the workflow_missing degraded state.
    result = ComfyUIProvider(ENDPOINT).generate(prompt="x", seed=1, workflow={})
    assert result["ok"] is False
    assert result["status"] == "workflow_missing"


def test_bundled_workflow_loads_and_has_placeholders():
    wf = comfyui._load_default_workflow()
    assert isinstance(wf, dict) and wf
    flat = str(wf)
    for token in (comfyui.PH_PROMPT, comfyui.PH_SEED, comfyui.PH_WIDTH,
                  comfyui.PH_HEIGHT, comfyui.PH_CHECKPOINT):
        assert token in flat


def test_bundled_workflow_has_no_hardcoded_checkpoint():
    # The committed workflow must stay portable — only the %checkpoint% token,
    # never a machine-specific checkpoint file.
    flat = str(comfyui._load_default_workflow()).lower()
    assert ".safetensors" not in flat
    assert ".ckpt" not in flat


# Checkpoint configuration (live-test blocker fix) --------------------------

def test_generate_substitutes_checkpoint_when_configured(monkeypatch):
    captured = {}

    def post(url, body):
        captured["wf"] = body["prompt"]
        return _FakeResp(200, {"prompt_id": "p"})

    def get(url, params):
        if "/history/" in url:
            return _FakeResp(200, _history_with_image("p"))
        return _FakeBytesResp(200, b"IMG")

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(
        prompt="a cat", seed=1, poll_interval=0, checkpoint="my_model.safetensors",
    )

    assert result["ok"] is True
    # Node 4 is the CheckpointLoaderSimple in the bundled workflow.
    assert captured["wf"]["4"]["inputs"]["ckpt_name"] == "my_model.safetensors"
    # No placeholder remains anywhere.
    assert not comfyui._workflow_has_placeholder(captured["wf"], comfyui.PH_CHECKPOINT)


def test_generate_requires_checkpoint_before_post(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not POST /prompt when checkpoint is missing")

    monkeypatch.setattr(comfyui.httpx, "post", boom)
    monkeypatch.setattr(comfyui.httpx, "get", boom)
    # Bundled workflow has %checkpoint%; no checkpoint supplied → fail early.
    result = ComfyUIProvider(ENDPOINT).generate(prompt="a cat", seed=1)

    assert result["ok"] is False
    assert result["status"] == "checkpoint_required"
    text = media_registry.format_degraded_message(result)
    assert "checkpoint" in text.lower()
    # Leak-safe: no URL / host / path in the message.
    assert "://" not in text and ENDPOINT not in text and "/Users/" not in text


def test_apply_workflow_params_leaves_checkpoint_when_none():
    wf = {"4": {"class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": comfyui.PH_CHECKPOINT}}}
    out = comfyui.apply_workflow_params(wf, prompt="p", seed=1, width=64, height=64)
    assert out["4"]["inputs"]["ckpt_name"] == comfyui.PH_CHECKPOINT  # unchanged
    out2 = comfyui.apply_workflow_params(
        wf, prompt="p", seed=1, width=64, height=64, checkpoint="x.safetensors",
    )
    assert out2["4"]["inputs"]["ckpt_name"] == "x.safetensors"


# F2: local-by-default endpoint enforcement ---------------------------------

REMOTE_ENDPOINT = "http://images.example.com:8188"


def test_classify_endpoint_tiers():
    # loopback / local machine
    for url in ("http://localhost:8188", "http://127.0.0.1:8188",
                "http://[::1]:8188", "http://box.localhost:8188"):
        assert comfyui.classify_endpoint(url) == comfyui.LOCALITY_LOOPBACK, url
    # Docker Desktop host bridge (container → Mac/Windows host)
    for url in ("http://host.docker.internal:8188",
                "http://gateway.docker.internal:8188",
                "http://HOST.DOCKER.INTERNAL:8188"):
        assert comfyui.classify_endpoint(url) == comfyui.LOCALITY_DOCKER_HOST, url
    # private LAN / local network (allowed, but distinct from loopback)
    for url in ("http://192.168.1.50:8188", "http://10.0.0.5:8188",
                "http://172.16.4.4:8188", "http://comfy.local:8188"):
        assert comfyui.classify_endpoint(url) == comfyui.LOCALITY_PRIVATE_LAN, url
    # public / remote
    for url in ("http://images.example.com:8188", "https://comfy.mycloud.io",
                "http://8.8.8.8:8188"):
        assert comfyui.classify_endpoint(url) == comfyui.LOCALITY_REMOTE, url
    # arbitrary *.internal names are NOT docker_host — still remote
    for url in ("http://comfy.internal:8188", "http://foo.docker.internal:8188"):
        assert comfyui.classify_endpoint(url) == comfyui.LOCALITY_REMOTE, url
    # unparseable / empty
    assert comfyui.classify_endpoint("") == comfyui.LOCALITY_UNKNOWN


def test_is_local_endpoint_classification():
    for local in (
        "http://localhost:8188",
        "http://127.0.0.1:8188",
        "http://[::1]:8188",
        "http://host.docker.internal:8188",
        "http://gateway.docker.internal:8188",
        "http://192.168.1.50:8188",
        "http://10.0.0.5:8188",
        "http://172.16.4.4:8188",
        "http://comfy.local:8188",
        "http://box.localhost:8188",
    ):
        assert comfyui.is_local_endpoint(local) is True, local
    for remote in (
        "http://images.example.com:8188",
        "https://comfy.mycloud.io",
        "http://8.8.8.8:8188",
        "http://comfy.internal:8188",
        "http://foo.docker.internal:8188",
    ):
        assert comfyui.is_local_endpoint(remote) is False, remote


def test_probe_allows_docker_host_bridge_without_remote_opt_in(monkeypatch):
    def router(url):
        return _FakeResp(200, {"system": {}})

    _install_router(monkeypatch, router)
    result = comfyui.probe("http://host.docker.internal:8188")
    assert result["status"] == "online"
    assert result["endpoint"] == "http://host.docker.internal:8188"


def test_probe_blocks_remote_by_default(monkeypatch):
    def boom(url, timeout=None):
        raise AssertionError("must not contact a remote endpoint when disallowed")

    monkeypatch.setattr(comfyui.httpx, "get", boom)
    result = ComfyUIProvider(REMOTE_ENDPOINT, allow_remote=False).probe()

    assert result["ok"] is False
    assert result["status"] == "remote_blocked"
    # F1: the blocked message must not leak the (remote) URL.
    text = media_registry.format_degraded_message(result)
    assert REMOTE_ENDPOINT not in text
    assert "example.com" not in text


def test_generate_blocks_remote_by_default(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not contact a remote endpoint when disallowed")

    monkeypatch.setattr(comfyui.httpx, "post", boom)
    monkeypatch.setattr(comfyui.httpx, "get", boom)
    result = ComfyUIProvider(REMOTE_ENDPOINT, allow_remote=False).generate(prompt="x", seed=1)

    assert result["ok"] is False
    assert result["status"] == "remote_blocked"
    assert "example.com" not in media_registry.format_degraded_message(result)


def test_remote_allowed_when_explicitly_enabled(monkeypatch):
    def post(url, body):
        return _FakeResp(200, {"prompt_id": "p"})

    def get(url, params):
        if "/history/" in url:
            return _FakeResp(200, _history_with_image("p"))
        return _FakeBytesResp(200, b"IMG")

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(REMOTE_ENDPOINT, allow_remote=True).generate(prompt="x", seed=1, checkpoint=CKPT)

    assert result["ok"] is True
    assert result["status"] == "generated"


def test_remote_allow_resolves_from_settings(monkeypatch):
    # allow_remote=None → provider reads the setting; default-missing means blocked.
    monkeypatch.setattr(comfyui, "_remote_media_allowed", lambda settings=None: False)

    def boom(*a, **k):
        raise AssertionError("must not contact remote endpoint")

    monkeypatch.setattr(comfyui.httpx, "post", boom)
    result = ComfyUIProvider(REMOTE_ENDPOINT).generate(prompt="x", seed=1)
    assert result["status"] == "remote_blocked"


# F1: provider errors never leak the endpoint URL ---------------------------

def test_generate_errors_never_leak_endpoint_url(monkeypatch):
    secret_local = "http://127.0.0.1:9999"

    # unreachable
    def post_unreach(url, body):
        raise httpx.ConnectError("connect to 127.0.0.1:9999 failed")

    _install_generation_router(monkeypatch, post=post_unreach, get=lambda *a: None)
    r1 = ComfyUIProvider(secret_local).generate(prompt="x", seed=1, checkpoint=CKPT)
    t1 = media_registry.format_degraded_message(r1)
    assert r1["status"] == "unreachable"
    assert "127.0.0.1" not in t1 and "9999" not in t1

    # generation_failed (queue HTTP error)
    _install_generation_router(monkeypatch, post=lambda u, b: _FakeResp(500, {}), get=lambda *a: None)
    r2 = ComfyUIProvider(secret_local).generate(prompt="x", seed=1, checkpoint=CKPT)
    t2 = media_registry.format_degraded_message(r2)
    assert r2["status"] == "generation_failed"
    assert "127.0.0.1" not in t2 and "9999" not in t2


# F4: prompt_id is URL-encoded before path interpolation ---------------------

def test_history_prompt_id_is_url_encoded(monkeypatch):
    weird_id = "a b/../c?x=1"

    def post(url, body):
        return _FakeResp(200, {"prompt_id": weird_id})

    def get(url, params):
        if "/history/" in url:
            # The raw id (with spaces / slashes / query chars) must not appear
            # verbatim in the request path.
            assert " " not in url
            assert "?" not in url.split("/history/")[1]
            assert "a%20b" in url  # space encoded
            return _FakeResp(200, _history_with_image(weird_id))
        return _FakeBytesResp(200, b"IMG")

    _install_generation_router(monkeypatch, post=post, get=get)
    result = ComfyUIProvider(ENDPOINT).generate(prompt="x", seed=1, poll_interval=0, checkpoint=CKPT)
    assert result["ok"] is True
