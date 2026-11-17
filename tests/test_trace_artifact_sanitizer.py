import json
import os
import shutil
import tempfile
import zipfile
import pytest

from scripts.sanitize_trace_artifacts import (
    sanitize_structured_obj,
    sanitize_jsonl_content,
    sanitize_url,
    sanitize_body_text,
    sanitize_trace_archive,
    sanitize_directory,
)


def test_sanitize_headers_and_inverted_keys():
    """Inverted key order and non-Bearer schemes are safely redacted."""
    # Standard order
    h1 = {"name": "Authorization", "value": "Bearer my_super_secret_jwt_12345"}
    assert sanitize_structured_obj(h1)["value"] == "[REDACTED]"

    # Inverted order {"value": ..., "name": ...}
    h2 = {"value": "Digest username=admin, nonce=abc, response=secret123", "name": "Authorization"}
    res2 = sanitize_structured_obj(h2)
    assert res2["value"] == "[REDACTED]"

    # ApiKey scheme
    h3 = {"name": "Authorization", "value": "ApiKey secret_api_key_xyz"}
    assert sanitize_structured_obj(h3)["value"] == "[REDACTED]"

    # Custom token header
    h4 = {"name": "X-Auth-Token", "value": "auth_token_999"}
    assert sanitize_structured_obj(h4)["value"] == "[REDACTED]"


def test_sanitize_cookies_arbitrary_names():
    """Arbitrary cookie names and values are completely redacted."""
    cookie_entry = {
        "cookies": [
            {"name": "odysseus_session", "value": "secret_sess_abc"},
            {"name": "remember_me_custom", "value": "remember_secret_val"},
            {"name": "sso_auth_id", "value": "sso_uuid_12345"},
        ]
    }
    sanitized = sanitize_structured_obj(cookie_entry)
    for c in sanitized["cookies"]:
        assert c["value"] == "[REDACTED]"


def test_sanitize_dom_snapshots_and_nested_action_params():
    """DOM snapshot trees and deeply nested action params are redacted."""
    # 1. Action with nested options before value
    action_record = {
        "type": "action",
        "name": "fill",
        "params": {
            "selector": "#login-password",
            "options": {"force": True, "timeout": 5000},
            "value": "escaped\"quote\"password123!",
        },
    }
    res_action = sanitize_structured_obj(action_record)
    assert res_action["params"]["value"] == "[REDACTED]"

    # 2. DOM snapshot input element
    dom_record = {
        "nodeName": "INPUT",
        "attributes": {
            "type": "password",
            "name": "user_password",
            "value": "super_secret_in_dom",
        },
    }
    res_dom = sanitize_structured_obj(dom_record)
    assert res_dom["attributes"]["value"] == "[REDACTED]"


def test_sanitize_url_tokens_and_authority():
    """URL query parameters and userinfo credentials are scrubbed."""
    # Authority credentials
    url1 = "https://admin:my_secret_pw@example.com/api/v1/resource"
    assert sanitize_url(url1) == "https://admin:[REDACTED]@example.com/api/v1/resource"

    # Query tokens
    url2 = "https://example.com/oauth/callback?code=auth_code_secret&state=xyz&token=access_token_123"
    sanitized2 = sanitize_url(url2)
    assert "auth_code_secret" not in sanitized2
    assert "access_token_123" not in sanitized2
    assert "code=%5BREDACTED%5D" in sanitized2 or "code=[REDACTED]" in sanitized2
    assert "token=%5BREDACTED%5D" in sanitized2 or "token=[REDACTED]" in sanitized2
    assert "state=xyz" in sanitized2


def test_sanitize_request_bodies():
    """JSON and urlencoded form request bodies are scrubbed."""
    # JSON body
    json_body = '{"username": "admin", "password": "superSecretPassword123!", "api_key": "sk-123456789"}'
    s_json = sanitize_body_text(json_body)
    assert "superSecretPassword123!" not in s_json
    assert "sk-123456789" not in s_json
    assert '"password": "[REDACTED]"' in s_json
    assert '"api_key": "[REDACTED]"' in s_json
    assert '"username": "admin"' in s_json

    # Form-encoded body
    form_body = "username=alice&password=mypassword999&token=abcde&action=login"
    s_form = sanitize_body_text(form_body)
    assert "mypassword999" not in s_form
    assert "abcde" not in s_form
    assert "password=%5BREDACTED%5D" in s_form or "password=[REDACTED]" in s_form
    assert "token=%5BREDACTED%5D" in s_form or "token=[REDACTED]" in s_form
    assert "username=alice" in s_form


def test_sanitize_valid_archive(tmp_path):
    """Structured archive sanitization cleans HAR, trace, and masks source code without corruption."""
    zp = str(tmp_path / "valid_trace.zip")
    with zipfile.ZipFile(zp, "w") as z:
        # JSON trace record
        trace_record = {
            "type": "action",
            "name": "fill",
            "params": {"selector": "#password", "value": 'secret"quote"pass'},
        }
        z.writestr("0-trace.trace", json.dumps(trace_record).encode() + b"\n")

        # Network record with inverted header keys and URL query
        net_record = {
            "type": "request",
            "request": {
                "url": "https://localhost:7013/api/auth/login?token=token_abc",
                "headers": [{"value": "Digest secret_digest", "name": "Authorization"}],
                "postData": '{"password": "my_request_pw"}',
            },
        }
        z.writestr("0-trace.network", json.dumps(net_record).encode() + b"\n")

        # Embedded test source file
        z.writestr("resources/src@test_auth.js", b'const password = "admin_password";\n')

        # Binary image screenshot
        z.writestr("screenshot.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdiagnostic")

    ok = sanitize_trace_archive(zp)
    assert ok is True
    assert os.path.exists(zp)

    with zipfile.ZipFile(zp, "r") as z:
        # Verify trace record
        trace_data = json.loads(z.read("0-trace.trace").decode().strip())
        assert trace_data["params"]["value"] == "[REDACTED]"

        # Verify network record
        net_data = json.loads(z.read("0-trace.network").decode().strip())
        assert "token_abc" not in net_data["request"]["url"]
        assert net_data["request"]["headers"][0]["value"] == "[REDACTED]"
        assert "my_request_pw" not in net_data["request"]["postData"]

        # Verify embedded source code was masked safely
        src_content = z.read("resources/src@test_auth.js").decode()
        assert "admin_password" not in src_content
        assert "[SOURCE CODE REDACTED FOR TRACE CONFIDENTIALITY]" in src_content
        assert "const password: [REDACTED]" not in src_content  # No syntax error!

        # Verify screenshot is unmodified
        png = z.read("screenshot.png")
        assert png == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdiagnostic"


def test_fail_closed_malformed_zip(tmp_path):
    zp = str(tmp_path / "corrupt.zip")
    with open(zp, "wb") as f:
        f.write(b"this is completely invalid zip data PK\x03\x04 corrupt")

    ok = sanitize_trace_archive(zp)
    assert ok is False
    # Fail-closed: corrupted zip must be deleted immediately!
    assert not os.path.exists(zp)


def test_fail_closed_partial_archive(tmp_path):
    zp = str(tmp_path / "partial.zip")
    with open(zp, "wb") as f:
        f.write(b"PK\x03\x04\x14\x00\x00\x00")

    ok = sanitize_trace_archive(zp)
    assert ok is False
    assert not os.path.exists(zp)


def test_fail_closed_empty_archive(tmp_path):
    zp = str(tmp_path / "empty.zip")
    with open(zp, "wb") as f:
        pass

    ok = sanitize_trace_archive(zp)
    assert ok is False
    assert not os.path.exists(zp)


def test_fail_closed_on_unhandled_exception(tmp_path, monkeypatch):
    zp = str(tmp_path / "valid.zip")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("test.trace", b'{"name": "fill"}\n')

    def broken_sanitize(obj):
        raise RuntimeError("Simulated crash")

    monkeypatch.setattr("scripts.sanitize_trace_artifacts.sanitize_structured_obj", broken_sanitize)

    ok = sanitize_trace_archive(zp)
    assert ok is False
    assert not os.path.exists(zp)
    assert not os.path.exists(zp + ".tmp")


def test_multiple_trace_archives_in_directory(tmp_path):
    sub1 = tmp_path / "test1"
    sub2 = tmp_path / "test2"
    sub1.mkdir()
    sub2.mkdir()

    z1 = str(sub1 / "trace.zip")
    z2 = str(sub2 / "trace.zip")

    with zipfile.ZipFile(z1, "w") as z:
        z.writestr("0.trace", b'{"type": "action", "params": {"value": "secret1"}}\n')
    with zipfile.ZipFile(z2, "w") as z:
        z.writestr("0.trace", b'{"type": "action", "params": {"value": "secret2"}}\n')

    count = sanitize_directory(str(tmp_path))
    assert count == 2

    assert os.path.exists(z1)
    with zipfile.ZipFile(z1, "r") as z:
        content = json.loads(z.read("0.trace").decode().strip())
        assert content["params"]["value"] == "[REDACTED]"

    assert os.path.exists(z2)
    with zipfile.ZipFile(z2, "r") as z:
        content = json.loads(z.read("0.trace").decode().strip())
        assert content["params"]["value"] == "[REDACTED]"
