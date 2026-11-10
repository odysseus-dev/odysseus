"""Inline-image vision path: direct image_url parts, downscale, probe, trimming.

Covers the change that sends attached images straight to the chat model as an
OpenAI image_url content part (instead of a separate vision-describe pre-step)
when the endpoint/model accepts images.
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from src import chat_helpers as ch
from src import document_processor as dp
from src.context_compactor import keep_recent_image_turns


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


# --- vision detection -------------------------------------------------------

def test_vision_model_env_override(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_VISION_MODELS", raising=False)
    assert ch.is_vision_model("ajax-ablated500-base") is False
    monkeypatch.setenv("ODYSSEUS_VISION_MODELS", "ajax, some-other")
    assert ch.is_vision_model("ajax-ablated500-base") is True
    assert ch.is_vision_model("SOME-OTHER-9B") is True
    assert ch.is_vision_model("plain-text-model") is False


def test_probe_endpoint_supports_vision_cached(monkeypatch):
    ch._vision_probe_cache.clear()
    calls = []

    class _Resp:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text

    def _fake_post(url, json=None, timeout=None):
        calls.append(url)
        return _Resp(200, "")

    monkeypatch.setattr(ch.httpx, "post", _fake_post)
    assert ch.probe_endpoint_supports_vision("http://127.0.0.1:18607/v1", "ajax-ablated500-base") is True
    # Second call is served from the cache (no extra POST).
    assert ch.probe_endpoint_supports_vision("http://127.0.0.1:18607/v1", "ajax-ablated500-base") is True
    assert len(calls) == 1
    assert calls[0].endswith("/v1/chat/completions")


def test_probe_endpoint_rejects_image(monkeypatch):
    ch._vision_probe_cache.clear()

    class _Resp:
        status_code = 400
        text = '{"error":"this model does not support image content"}'

    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: _Resp())
    assert ch.probe_endpoint_supports_vision("http://127.0.0.1:18607/v1", "text-only-9b") is False


def test_probe_never_probes_remote_hosts(monkeypatch):
    ch._vision_probe_cache.clear()
    calls = []
    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: calls.append(a) or None)
    assert ch.probe_endpoint_supports_vision("https://api.openai.com/v1", "gpt-x") is None
    assert calls == []


def test_model_supports_vision_uses_probe_for_local(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_VISION_MODELS", raising=False)
    monkeypatch.setattr(ch, "lmstudio_supports_vision", lambda *a, **k: None)
    monkeypatch.setattr(ch, "probe_endpoint_supports_vision", lambda url, model: True)
    assert ch.model_supports_vision("ajax-ablated500-base", "http://127.0.0.1:18607/v1") is True


# --- downscale --------------------------------------------------------------

def test_encode_image_data_url_downscales_to_jpeg(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(_png_bytes(2400, 600))
    data_url, fmt = dp.encode_image_data_url(str(path), "png")
    assert fmt == "jpeg"
    assert data_url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        assert max(img.size) <= 1280
        # aspect ratio preserved
        assert round(img.size[0] / img.size[1], 2) == 4.0


def test_encode_image_data_url_small_image_ok(tmp_path):
    path = tmp_path / "small.png"
    path.write_bytes(_png_bytes(64, 48))
    data_url, fmt = dp.encode_image_data_url(str(path), "png")
    assert fmt == "jpeg"
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        assert img.size == (64, 48)


# --- keep last N image turns ------------------------------------------------

def _user_with_image(caption: str, text: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
        "_caption": caption,
    }


def test_keep_recent_image_turns_labels_old_images():
    msgs = [
        {"role": "user", "content": "hello"},
        _user_with_image("a.png", "[Image attached: a.png]\nlook at a"),
        {"role": "assistant", "content": "sure"},
        _user_with_image("b.png", "[Image attached: b.png]\nlook at b"),
        _user_with_image("c.png", "[Image attached: c.png]\nlook at c"),
    ]
    out = keep_recent_image_turns(msgs, keep=2)

    def _img_count(m):
        c = m.get("content")
        return sum(1 for b in c if isinstance(b, dict) and b.get("type") == "image_url") if isinstance(c, list) else 0

    # first (oldest) image turn lost its image part, gained the label
    assert _img_count(out[1]) == 0
    assert "[Image: a.png]" in "".join(
        b.get("text", "") for b in out[1]["content"] if isinstance(b, dict)
    )
    # two most recent image turns keep their image parts
    assert _img_count(out[3]) == 1
    assert _img_count(out[4]) == 1
    # non-image message untouched (identity preserved for strings)
    assert out[0]["content"] == "hello"


def test_keep_recent_image_turns_noop_when_few_images():
    msgs = [_user_with_image("a.png", "[Image attached: a.png]")]
    out = keep_recent_image_turns(msgs, keep=2)
    assert out == msgs


def test_keep_recent_image_turns_does_not_mutate_original_history():
    import copy
    messages = [_user_with_image('a.png', 'hello'), _user_with_image('b.png', 'later')]
    original = copy.deepcopy(messages)
    result = keep_recent_image_turns(messages, keep=1)
    assert messages == original
    assert '[Image]' in str(result[0]['content'])


def test_old_image_with_string_url_does_not_break_history_compaction():
    messages = [
        {'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'data:image/png;base64,AAAA'}]},
        _user_with_image('later.png', 'later'),
    ]
    result = keep_recent_image_turns(messages, keep=1)
    assert result[0]['content'] == [{'type': 'text', 'text': '[Image]'}]


# --- vision probe security & authorization regressions -----------------------

def test_probe_blocks_link_local_and_cloud_metadata(monkeypatch):
    ch._vision_probe_cache.clear()
    calls = []
    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: calls.append(a) or None)

    disallowed_urls = [
        "http://169.254.169.254/v1",
        "http://169.254.169.253/v1",
        "http://169.254.1.1/v1",
        "http://[fe80::1]/v1",
        "http://100.100.100.200/v1",
        "http://instance-data/v1",
        "http://metadata.google.internal/v1",
    ]
    for url in disallowed_urls:
        assert ch.probe_endpoint_supports_vision(url, "some-model") is None
        assert ch.lmstudio_supports_vision(url, "some-model") is None
    assert calls == []


def test_probe_blocks_public_domains(monkeypatch):
    ch._vision_probe_cache.clear()
    calls = []
    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: calls.append(a) or None)

    public_urls = [
        "https://api.openai.com/v1",
        "https://generativelanguage.googleapis.com/v1",
        "http://example.com/v1",
    ]
    for url in public_urls:
        assert ch.probe_endpoint_supports_vision(url, "gpt-4o") is None
        assert ch.lmstudio_supports_vision(url, "gpt-4o") is None
    assert calls == []


def test_model_supports_vision_enforces_owner_authorization(tmp_path, monkeypatch):
    from tests.helpers.database import disposable_database
    from core import database

    post_calls = []

    class _Resp:
        status_code = 200
        text = '{"choices":[]}'

    monkeypatch.setattr(ch, "is_vision_model", lambda m: False)
    monkeypatch.setattr(ch.httpx, "post", lambda *a, **k: post_calls.append((a, k)) or _Resp())
    monkeypatch.setattr(ch, "lmstudio_supports_vision", lambda *a, **k: None)

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr("src.database.SessionLocal", factory)
        with factory() as db:
            db.add(database.ModelEndpoint(
                id="alice-local",
                name="Alice Local",
                owner="alice",
                base_url="http://127.0.0.1:18607/v1",
                api_key="alice-secret",
                is_enabled=True,
            ))
            db.add(database.ModelEndpoint(
                id="alice-disabled",
                name="Alice Disabled",
                owner="alice",
                base_url="http://127.0.0.1:18608/v1",
                api_key="alice-secret",
                is_enabled=False,
            ))
            db.commit()

        # 1. Foreign owner cannot probe alice's endpoint
        ch._vision_probe_cache.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18607/v1", owner="bob") is False
        assert len(post_calls) == 0

        # 2. Disabled endpoint is never probed
        ch._vision_probe_cache.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18608/v1", owner="alice") is False
        assert len(post_calls) == 0

        # 3. Unregistered endpoint is never probed when owner is provided
        ch._vision_probe_cache.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18609/v1", owner="alice") is False
        assert len(post_calls) == 0

        # 4. Registered, enabled endpoint owned by alice IS probed with registered runtime credentials
        ch._vision_probe_cache.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18607/v1", owner="alice") is True
        assert len(post_calls) == 1
        args, kwargs = post_calls[0]
        assert "http://127.0.0.1:18607/v1/chat/completions" in args[0]
        assert kwargs.get("headers", {}).get("Authorization") == "Bearer alice-secret"


def test_is_ip_safe_local_ipv6_and_mapped_addresses():
    import ipaddress
    # IPv4 loopback & IPv6 loopback must be allowed
    assert ch._is_ip_safe_local(ipaddress.ip_address("127.0.0.1")) is True
    assert ch._is_ip_safe_local(ipaddress.ip_address("::1")) is True

    # Cloud metadata destinations must be blocked
    assert ch._is_ip_safe_local(ipaddress.ip_address("169.254.169.254")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("169.254.169.253")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("100.100.100.200")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("fe80::1")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("169.254.1.1")) is False

    # IPv4-mapped IPv6 addresses must be unwrapped and judged as IPv4
    assert ch._is_ip_safe_local(ipaddress.ip_address("::ffff:169.254.169.254")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("::ffff:100.100.100.200")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("::ffff:127.0.0.1")) is True
    assert ch._is_ip_safe_local(ipaddress.ip_address("::ffff:192.168.1.1")) is True

    # NAT64 translated addresses must be unwrapped
    assert ch._is_ip_safe_local(ipaddress.ip_address("64:ff9b::169.254.169.254")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("64:ff9b::127.0.0.1")) is True

    # Unspecified, multicast, and non-loopback reserved addresses must be blocked
    assert ch._is_ip_safe_local(ipaddress.ip_address("::")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("0.0.0.0")) is False
    assert ch._is_ip_safe_local(ipaddress.ip_address("ff02::1")) is False


def test_is_safe_local_host_handles_ipv6_and_dual_stack(monkeypatch):
    import socket
    # Direct literals
    assert ch._is_safe_local_host("::1") is True
    assert ch._is_safe_local_host("[::1]") is True
    assert ch._is_safe_local_host("127.0.0.1") is True

    # Dual-stack localhost where getaddrinfo returns both IPv6 ::1 and IPv4 127.0.0.1
    def fake_dual_stack_getaddrinfo(host, port):
        return [
            (socket.AF_INET6, 1, 6, "", ("::1", 0, 0, 0)),
            (socket.AF_INET, 1, 6, "", ("127.0.0.1", 0)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", fake_dual_stack_getaddrinfo)
    assert ch._is_safe_local_host("localhost") is True

    # Mixed DNS resolution (one valid loopback, one metadata destination) must be rejected
    def fake_mixed_getaddrinfo(host, port):
        return [
            (socket.AF_INET, 1, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, 1, 6, "", ("169.254.169.254", 0)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", fake_mixed_getaddrinfo)
    assert ch._is_safe_local_host("rig.local") is False


def test_bracketed_ipv6_url_handling(monkeypatch):
    calls = []

    class _Resp:
        status_code = 200
        text = '{"models":[{"key":"model1","architecture":{},"capabilities":{"vision":true}}]}'
        is_success = True
        def json(self):
            import json
            return json.loads(self.text)

    monkeypatch.setattr(ch, "_safe_probe_get", lambda url, **k: calls.append(url) or _Resp())
    ch._lmstudio_models_cache.clear()
    models = ch._probe_lmstudio_models("http://[::1]:18607/v1")
    assert models is not None
    assert calls == ["http://[::1]:18607/api/v1/models"]


def test_connection_time_address_enforcement_blocks_unsafe_destinations():
    backend = ch.SafeProbeBackend()
    import httpcore
    import pytest
    with pytest.raises(httpcore.ConnectError, match="not an authorized safe local destination"):
        backend.connect_tcp("169.254.169.254", 80, timeout=0.1)

    with pytest.raises(httpcore.ConnectError, match="not an authorized safe local destination"):
        backend.connect_tcp("100.100.100.200", 80, timeout=0.1)


def test_vision_probe_never_follows_redirects(monkeypatch):
    class _RedirectResp:
        status_code = 302
        headers = {"Location": "http://169.254.169.254/secret"}
        is_success = False
        text = ""

    # Verify _probe_lmstudio_models does not follow redirects
    monkeypatch.setattr(ch, "_safe_probe_get", lambda url, **k: _RedirectResp())
    ch._lmstudio_models_cache.clear()
    assert ch._probe_lmstudio_models("http://127.0.0.1:18607/v1") is None

    # Verify probe_endpoint_supports_vision does not follow redirects
    monkeypatch.setattr(ch, "_safe_probe_post", lambda url, **k: _RedirectResp())
    ch._vision_probe_cache.clear()
    assert ch.probe_endpoint_supports_vision("http://127.0.0.1:18607/v1", "text-only-9b") is None


def test_model_supports_vision_auth_policy_matrix(tmp_path, monkeypatch):
    from tests.helpers.database import disposable_database
    from core import database

    post_calls = []

    class _Resp:
        status_code = 200
        text = '{"choices":[]}'

    monkeypatch.setattr(ch, "is_vision_model", lambda m: False)
    monkeypatch.setattr(ch, "lmstudio_supports_vision", lambda *a, **k: None)
    monkeypatch.setattr(ch, "_safe_probe_post", lambda *a, **k: post_calls.append((a, k)) or _Resp())

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr("src.database.SessionLocal", factory)
        with factory() as db:
            db.add(database.ModelEndpoint(
                id="alice-local",
                name="Alice Local",
                owner="alice",
                base_url="http://127.0.0.1:18607/v1",
                api_key="alice-key",
                is_enabled=True,
            ))
            db.add(database.ModelEndpoint(
                id="unowned-local",
                name="Unowned Local",
                owner=None,
                base_url="http://127.0.0.1:18610/v1",
                api_key="unowned-key",
                is_enabled=True,
            ))
            db.commit()

        # Case 1: Auth disabled, explicitly configured local endpoint -> probes with runtime credentials
        monkeypatch.setenv("AUTH_ENABLED", "false")
        ch._vision_probe_cache.clear()
        post_calls.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18610/v1", owner=None) is True
        assert len(post_calls) == 1
        assert post_calls[0][1].get("headers", {}).get("Authorization") == "Bearer unowned-key"

        # Case 2: Auth disabled, unregistered caller-supplied endpoint -> allowed if safe local, probed without credentials
        monkeypatch.setenv("AUTH_ENABLED", "false")
        ch._vision_probe_cache.clear()
        post_calls.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18611/v1", owner=None) is True
        assert len(post_calls) == 1
        assert post_calls[0][1].get("headers") is None

        # Case 3: Auth enabled, missing owner ("" or None from unowned session) -> rejected immediately
        monkeypatch.setenv("AUTH_ENABLED", "true")
        ch._vision_probe_cache.clear()
        post_calls.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18607/v1", owner="") is False
        assert len(post_calls) == 0

        # Case 4: Auth enabled, foreign-owned endpoint -> rejected immediately
        monkeypatch.setenv("AUTH_ENABLED", "true")
        ch._vision_probe_cache.clear()
        post_calls.clear()
        assert ch.model_supports_vision("text-only-9b", "http://127.0.0.1:18607/v1", owner="bob") is False
        assert len(post_calls) == 0
