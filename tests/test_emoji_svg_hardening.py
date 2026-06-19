import asyncio

from routes import emoji_routes


def _emoji_endpoint():
    router = emoji_routes.setup_emoji_routes()
    for route in router.routes:
        if route.path == "/api/emoji/{code}.svg" and "GET" in route.methods:
            return route.endpoint
    raise AssertionError("emoji route not found")


def test_svg_safety_rejects_active_or_external_svg_content():
    assert emoji_routes._is_safe_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    )

    assert not emoji_routes._is_safe_svg(b'<svg><script>alert(1)</script></svg>')
    assert not emoji_routes._is_safe_svg(b'<svg onload="alert(1)"></svg>')
    assert not emoji_routes._is_safe_svg(b'<svg><image href="https://example.com/x.png"/></svg>')
    assert not emoji_routes._is_safe_svg(b"<svg>" + b"a" * (emoji_routes._MAX_SVG_BYTES + 1))


def test_cached_svg_served_with_security_headers(tmp_path, monkeypatch):
    cache_dir = tmp_path / "emoji"
    cache_dir.mkdir()
    monkeypatch.setattr(emoji_routes, "_CACHE_DIR", cache_dir)
    content = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    (cache_dir / "1f600.svg").write_bytes(content)

    response = asyncio.run(_emoji_endpoint()("1f600"))

    assert response.body == content
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"


def test_cached_active_svg_returns_blank_and_evicts_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "emoji"
    cache_dir.mkdir()
    monkeypatch.setattr(emoji_routes, "_CACHE_DIR", cache_dir)
    cached = cache_dir / "1f600.svg"
    cached.write_bytes(b'<svg onload="alert(1)"></svg>')

    response = asyncio.run(_emoji_endpoint()("1f600"))

    assert response.body == emoji_routes._BLANK_SVG
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
    assert not cached.exists()
