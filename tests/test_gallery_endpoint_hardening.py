"""Focused security tests for gallery endpoint URL hardening.

Covers:
- _is_openai_api_base: exact hostname matching (no substring bypass)
- _join_checked_gallery_endpoint: allowlist-only path construction
- No bare str(e) / f"...{e}" in gallery exception handlers
- harmonize validates _endpoint via check_outbound_url
- Target URL construction only appends constant paths to the validated base
"""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "routes" / "gallery" / "gallery_routes.py"

import routes.gallery_routes as gallery_routes


# ---------------------------------------------------------------------------
# _is_openai_api_base — exact hostname, no substring tricks
# ---------------------------------------------------------------------------

def test_is_openai_api_base_accepts_exact_host():
    f = gallery_routes._is_openai_api_base
    assert f("https://api.openai.com") is True
    assert f("https://api.openai.com/v1") is True
    assert f("https://api.openai.com/") is True
    assert f("api.openai.com") is True


def test_is_openai_api_base_rejects_path_embed():
    # attacker hides api.openai.com in the path, not the hostname
    f = gallery_routes._is_openai_api_base
    assert f("https://evil.test/api.openai.com/v1") is False


def test_is_openai_api_base_rejects_subdomain_suffix():
    # hostname ends with .openai.com but isn't exactly api.openai.com
    f = gallery_routes._is_openai_api_base
    assert f("https://api.openai.com.evil.test/v1") is False
    assert f("https://evil-api.openai.com/v1") is False
    assert f("https://notapi.openai.com/v1") is False


def test_is_openai_api_base_rejects_malformed():
    f = gallery_routes._is_openai_api_base
    assert f("") is False
    assert f("not a url at all !!!") is False


# ---------------------------------------------------------------------------
# Source-level: gallery no longer uses substring "api.openai.com" in base
# ---------------------------------------------------------------------------

def test_gallery_does_not_use_openai_substring_check():
    src = SRC.read_text()
    assert '"api.openai.com" in base' not in src, (
        "Substring OpenAI check still present — use _is_openai_api_base instead"
    )
    assert "'api.openai.com' in base" not in src, (
        "Substring OpenAI check still present — use _is_openai_api_base instead"
    )


# ---------------------------------------------------------------------------
# _join_checked_gallery_endpoint — allowlist enforcement
# ---------------------------------------------------------------------------

def test_join_checked_accepts_known_paths():
    j = gallery_routes._join_checked_gallery_endpoint
    assert j("http://localhost:7860/v1", "/images/img2img") == "http://localhost:7860/v1/images/img2img"
    assert j("http://localhost:7860", "/sdapi/v1/img2img") == "http://localhost:7860/sdapi/v1/img2img"
    assert j("https://api.openai.com/v1", "/images/edits") == "https://api.openai.com/v1/images/edits"


def test_join_checked_rejects_unknown_path():
    import pytest
    j = gallery_routes._join_checked_gallery_endpoint
    with pytest.raises(ValueError):
        j("http://localhost/v1", "/arbitrary/user/path")
    with pytest.raises(ValueError):
        j("http://localhost/v1", "")
    with pytest.raises(ValueError):
        j("http://localhost/v1", "https://evil.test/steal")


# ---------------------------------------------------------------------------
# Source-level: no raw str(e) / f"...{e}" returned to API clients
# ---------------------------------------------------------------------------

def test_no_raw_exception_string_in_client_responses():
    src = SRC.read_text()
    # Patterns that indicate exception internals flowing into client-visible values.
    # We allow them only in logger calls (checked separately below).
    bad_patterns = [
        r'return \{"error": str\(e\)\}',
        r'return \{"error": f"[^"]*\{e\}[^"]*"\}',
        r'HTTPException\(\d+, str\(e\)\)',
        r'HTTPException\(\d+, f"[^"]*\{e\}[^"]*"\)',
    ]
    for pattern in bad_patterns:
        matches = re.findall(pattern, src)
        assert not matches, (
            f"Pattern {pattern!r} matched — raw exception string returned to client: {matches}"
        )


# ---------------------------------------------------------------------------
# harmonize: validates _endpoint via check_outbound_url before outbound request
# ---------------------------------------------------------------------------

def _function_source(src_text: str, func_name: str) -> str:
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(src_text, node) or ""
    raise AssertionError(f"{func_name} not found in {SRC}")


def test_harmonize_validates_endpoint_before_fetch():
    src = SRC.read_text()
    body = _function_source(src, "harmonize_image")
    assert "check_outbound_url" in body, (
        "harmonize_image must validate _endpoint via check_outbound_url before outbound requests"
    )


# ---------------------------------------------------------------------------
# harmonize: target URL only appends constant allowed paths
# ---------------------------------------------------------------------------

def test_harmonize_uses_join_checked_for_target_construction():
    src = SRC.read_text()
    body = _function_source(src, "harmonize_image")
    assert "_join_checked_gallery_endpoint" in body, (
        "harmonize_image must use _join_checked_gallery_endpoint to build target URLs"
    )
    # Raw concatenation patterns that bypass the allowlist must not appear in harmonize
    assert "base_root + path" not in body, (
        "harmonize_image must not concatenate base_root + path directly"
    )
    assert "base + path" not in body, (
        "harmonize_image must not concatenate base + path directly"
    )


def test_gallery_endpoint_paths_allowlist_covers_all_harmonize_candidates():
    # Every path in the candidates list must be in the pre-approved allowlist.
    src = SRC.read_text()
    body = _function_source(src, "harmonize_image")
    # Extract string literals that look like route paths from candidates
    candidate_paths = re.findall(r'"/(?:images|sdapi)/[^"]*"', body)
    allowed = gallery_routes._GALLERY_ENDPOINT_PATHS
    for p in candidate_paths:
        p = p.strip('"')
        assert p in allowed, (
            f"Path {p!r} used in harmonize candidates but not in _GALLERY_ENDPOINT_PATHS allowlist"
        )


# ---------------------------------------------------------------------------
# _is_openai_api_base — userinfo bypass
# ---------------------------------------------------------------------------

def test_is_openai_api_base_rejects_userinfo_bypass():
    # userinfo trick: user = api.openai.com, host = evil.test
    f = gallery_routes._is_openai_api_base
    assert f("https://api.openai.com@evil.test/v1") is False


# ---------------------------------------------------------------------------
# Source-level: no client-visible error leaks upstream body fragments
# ---------------------------------------------------------------------------

def _extract_httpexception_call(src: str, pos: int) -> str:
    """Paren-match from the opening '(' of an HTTPException call."""
    start = src.index("(", pos)
    depth = 0
    for k, ch in enumerate(src[start:]):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[start : start + k + 1]
    return src[start:]


def test_no_upstream_data_in_client_responses():
    """No raise HTTPException or return {"error": ...} may expose upstream body data."""
    src = SRC.read_text()
    forbidden = [
        "r.text",
        "body[:",
        'data["error"]',
        "data['error']",
        "last_err",
        "{base}",
    ]

    for m in re.finditer(r"\braise\s+HTTPException\s*\(", src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if "logger." in src[line_start : m.start()]:
            continue
        call_text = _extract_httpexception_call(src, m.start())
        for frag in forbidden:
            assert frag not in call_text, (
                f"HTTPException raise at byte {m.start()} exposes {frag!r} to client:\n{call_text[:300]}"
            )

    for m in re.finditer(r'return\s*\{"error":', src):
        line_end = src.find("\n", m.start())
        line = src[m.start() : line_end if line_end != -1 else len(src)]
        for frag in forbidden:
            assert frag not in line, (
                f"Error return at byte {m.start()} exposes {frag!r} to client:\n{line}"
            )


# ---------------------------------------------------------------------------
# inpaint_proxy: endpoint construction via _join_checked_gallery_endpoint
# ---------------------------------------------------------------------------

def test_inpaint_uses_join_checked_endpoint():
    src = SRC.read_text()
    body = _function_source(src, "inpaint_proxy")
    assert 'f"{base}/images/edits"' not in body, (
        "inpaint_proxy must not build /images/edits via raw f-string"
    )
    assert 'f"{base}/images/inpaint"' not in body, (
        "inpaint_proxy must not build /images/inpaint via raw f-string"
    )
    assert '_join_checked_gallery_endpoint(base, "/images/edits")' in body, (
        "inpaint_proxy must use _join_checked_gallery_endpoint for /images/edits"
    )
    assert '_join_checked_gallery_endpoint(base, "/images/inpaint")' in body, (
        "inpaint_proxy must use _join_checked_gallery_endpoint for /images/inpaint"
    )


# ---------------------------------------------------------------------------
# harmonize final 502: no base URL or last_err in client message
# ---------------------------------------------------------------------------

def test_harmonize_final_502_omits_base_and_last_err():
    src = SRC.read_text()
    body = _function_source(src, "harmonize_image")
    # Collect all HTTPException raises in harmonize and check the last one (final 502)
    raises = list(re.finditer(r"\braise\s+HTTPException\s*\(", body))
    assert raises, "harmonize_image must contain at least one raise HTTPException"
    last_call = _extract_httpexception_call(body, raises[-1].start())
    for forbidden in ("last_err", "{base}", "r.text"):
        assert forbidden not in last_call, (
            f"harmonize final raise exposes {forbidden!r} to client:\n{last_call}"
        )


# ---------------------------------------------------------------------------
# inpaint/harmonize: _endpoint must resolve via DB; no raw admin bypass
# ---------------------------------------------------------------------------

def test_inpaint_endpoint_resolved_via_db_not_raw_input():
    """inpaint_proxy must not use the raw request-body value as the outbound base.
    The user-supplied value is stored as requested_base; outbound base comes from DB."""
    src = SRC.read_text()
    body = _function_source(src, "inpaint_proxy")
    # requested_base holds the user input; base is only set from ep.base_url
    assert "requested_base" in body, (
        "inpaint_proxy must use 'requested_base' for the user-supplied value"
    )
    # The admin bypass (not _current_user_is_admin) must not appear in inpaint
    assert "_current_user_is_admin" not in body, (
        "inpaint_proxy must not have an admin bypass for raw endpoint resolution"
    )
    # If no matching endpoint is found, a 403 must be raised unconditionally
    assert 'raise HTTPException(403, "Choose a registered image endpoint")' in body, (
        "inpaint_proxy must raise 403 when _endpoint doesn't match a registered endpoint"
    )


def test_inpaint_outbound_base_not_from_request_body():
    """Confirm _join_checked_gallery_endpoint is never called with the raw
    request-body variable (requested_base) — only with the DB-derived base."""
    src = SRC.read_text()
    body = _function_source(src, "inpaint_proxy")
    assert "_join_checked_gallery_endpoint(requested_base," not in body, (
        "inpaint_proxy must not pass requested_base to _join_checked_gallery_endpoint"
    )


def test_harmonize_endpoint_resolved_via_db_not_raw_input():
    """harmonize_image must not use the raw request-body value as the outbound base."""
    src = SRC.read_text()
    body = _function_source(src, "harmonize_image")
    assert "requested_base" in body, (
        "harmonize_image must use 'requested_base' for the user-supplied value"
    )
    assert "_current_user_is_admin" not in body, (
        "harmonize_image must not have an admin bypass for raw endpoint resolution"
    )
    assert 'raise HTTPException(403, "Choose a registered image endpoint")' in body, (
        "harmonize_image must raise 403 when _endpoint doesn't match a registered endpoint"
    )


def test_harmonize_outbound_base_not_from_request_body():
    """Confirm _join_checked_gallery_endpoint is never called with requested_base."""
    src = SRC.read_text()
    body = _function_source(src, "harmonize_image")
    assert "_join_checked_gallery_endpoint(requested_base," not in body, (
        "harmonize_image must not pass requested_base to _join_checked_gallery_endpoint"
    )


def test_inpaint_and_harmonize_no_base_equals_endpoint():
    """Neither function should assign `base = endpoint` or `base = requested_base`
    — the outbound base must come exclusively from DB (ep.base_url)."""
    src = SRC.read_text()
    for func_name in ("inpaint_proxy", "harmonize_image"):
        body = _function_source(src, func_name)
        assert "base = endpoint" not in body, (
            f"{func_name}: 'base = endpoint' carries request-body input into outbound request"
        )
        assert "base = requested_base" not in body, (
            f"{func_name}: 'base = requested_base' carries request-body input into outbound request"
        )
