"""Tests for OXS manifest schema validation.

Validates the ``extension.json`` contract in ``core/extensions/manifest_schema.py``.
The validator is fail-closed: every deviation from the schema must raise
``OXSManifestError`` with a list of human-readable errors.
"""

from __future__ import annotations

import json

import pytest

from core.extensions import (
    OXSManifestError,
    validate_manifest,
)


def _minimal_manifest(**overrides) -> dict:
    """Return a manifest that passes schema validation.

    Override any field via kwargs to break it. Tests mutate a copy so the
    fixture stays reusable across cases.
    """
    base = {
        "id": "test-extension",
        "name": "Test Extension",
        "version": "1.0.0",
        "oxs_version": ">=1.0.0,<2.0.0",
        "entry_point": "plugin:register",
        "hooks": ["on_enable", "on_disable"],
        "capabilities": {
            "data": {"own_tables": ["messages"]},
        },
    }
    base.update(overrides)
    return base


# ── Happy path ────────────────────────────────────────────────────────────


def test_minimal_manifest_validates():
    m = _minimal_manifest()
    result = validate_manifest(m)
    assert result is m
    assert result["id"] == "test-extension"


def test_manifest_accepts_json_string():
    raw = json.dumps(_minimal_manifest())
    result = validate_manifest(raw)
    assert result["id"] == "test-extension"


def test_manifest_accepts_bytes():
    raw = json.dumps(_minimal_manifest()).encode("utf-8")
    result = validate_manifest(raw)
    assert result["id"] == "test-extension"


def test_manifest_with_safe_capabilities_dict():
    m = _minimal_manifest(capabilities={
        "data":       {"own_tables": ["a", "b"]},
        "routes":     {"prefix": "/api/ext/x", "auth": "user"},
        "tools":      [{"name": "x", "description": "..."}],
        "static":     True,
        "ui_sidebar": {"id": "x", "icon": "chat", "label": "X"},
        "settings_read": True,
    })
    validate_manifest(m)


def test_manifest_with_medium_capabilities_dict():
    m = _minimal_manifest(capabilities={
        "network_outbound":        {"destinations": ["https://api.example.com"]},
        "filesystem_data_subpath": {"paths": ["media/"]},
    })
    validate_manifest(m)


def test_manifest_with_hooks():
    m = _minimal_manifest(hooks=["on_install", "on_enable", "on_disable", "on_uninstall"])
    validate_manifest(m)


def test_manifest_with_full_metadata():
    m = _minimal_manifest(
        python_deps=["httpx>=0.27", "pydantic"],
        author={"name": "Alice", "email": "alice@example.com", "url": "https://alice.dev"},
        license="MIT",
        repository="https://github.com/alice/extension",
        file_hashes={
            "plugin.py": "sha256:" + "a" * 64,
            "ui/index.html": "sha256:" + "b" * 64,
        },
    )
    validate_manifest(m)


def test_real_hello_odysseus_manifest_validates():
    """The reference extension shipped in extensions/ must pass validation."""
    import os
    path = os.path.join(
        os.path.dirname(__file__), "..", "extensions",
        "hello-odysseus", "extension.json",
    )
    with open(os.path.abspath(path)) as f:
        m = json.load(f)
    validate_manifest(m)


# ── Identity errors ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_id", [
    "", "Has Spaces", "UPPER", "a" * 65, ".leading-dot", "-leading-dash",
    "weird/slash", "weird\\backslash",
])
def test_invalid_extension_id_rejected(bad_id):
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(id=bad_id))
    assert any("manifest.id" in e for e in exc_info.value.errors)


@pytest.mark.parametrize("bad_version", [
    "1.0", "1", "1.0.0.0", "v1.0.0", "1.0.0+invalid+build", "not.a.version",
])
def test_invalid_semver_rejected(bad_version):
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(version=bad_version))
    assert any("manifest.version" in e for e in exc_info.value.errors)


@pytest.mark.parametrize("bad_range", [
    "not a range", "", "@@invalid@@", ">>1.0.0",
])
def test_invalid_pep440_range_rejected(bad_range):
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(oxs_version=bad_range))
    assert any("manifest.oxs_version" in e for e in exc_info.value.errors)


@pytest.mark.parametrize("bad_ep", [
    "", "register", "module:", ":register", "module:callable:extra",
    "1module:register", "module:1callable",
])
def test_invalid_entry_point_rejected(bad_ep):
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(entry_point=bad_ep))
    assert any("manifest.entry_point" in e for e in exc_info.value.errors)


# ── Capability errors ─────────────────────────────────────────────────────


def test_unknown_capability_rejected():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(capabilities={"not_a_capability": True}))
    assert any("not_a_capability" in e for e in exc_info.value.errors)


def test_privileged_capability_in_wrong_block_rejected():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(capabilities={"shell_command": True}))
    assert any("privileged_permissions" in e for e in exc_info.value.errors)


def test_medium_capability_must_be_dict():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(capabilities={"network_outbound": True}))
    assert any("network_outbound" in e for e in exc_info.value.errors)


def test_safe_capability_wrong_type():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(capabilities={"data": "garbage"}))
    assert any("data" in e for e in exc_info.value.errors)


# ── Permission errors ─────────────────────────────────────────────────────


def test_network_outbound_must_be_list():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(permissions={"network_outbound": "https://x"}))
    assert any("network_outbound" in e for e in exc_info.value.errors)


def test_network_outbound_url_must_have_scheme():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(permissions={"network_outbound": ["api.example.com"]}))
    assert any("http(s)://" in e for e in exc_info.value.errors)


def test_filesystem_data_subpath_rejects_traversal():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(permissions={"filesystem_data_subpath": ["../escape"]}))
    assert any(".." in e for e in exc_info.value.errors)


def test_filesystem_data_subpath_rejects_absolute():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(permissions={"filesystem_data_subpath": ["/etc/passwd"]}))
    assert any("leading slash" in e for e in exc_info.value.errors)


def test_unknown_permission_key_rejected():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(permissions={"unknown_permission": []}))
    assert any("not a known permission" in e for e in exc_info.value.errors)


# ── Privileged permission errors ───────────────────────────────────────────


def test_privileged_permission_unknown_rejected():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(privileged_permissions={"backdoor": True}))
    assert any("not a known privileged capability" in e for e in exc_info.value.errors)


def test_privileged_permission_must_be_boolean():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(privileged_permissions={"shell_command": "yes"}))
    assert any("must be a boolean" in e for e in exc_info.value.errors)


def test_privileged_permissions_explicit_false_ok():
    m = _minimal_manifest(privileged_permissions={
        "shell_command": False,
        "admin_routes": False,
    })
    validate_manifest(m)


# ── File hash errors ───────────────────────────────────────────────────────


def test_file_hash_wrong_format():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(file_hashes={
            "plugin.py": "md5:" + "a" * 32,
        }))
    assert any("sha256:" in e for e in exc_info.value.errors)


def test_file_hash_short_hex():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(file_hashes={
            "plugin.py": "sha256:" + "a" * 63,
        }))
    assert any("64 hex chars" in e for e in exc_info.value.errors)


def test_file_hash_uppercase_hex_ok():
    # We lowercase at render, but the parser should accept uppercase.
    upper = "sha256:" + "F" * 64
    m = _minimal_manifest(file_hashes={"plugin.py": upper})
    validate_manifest(m)


# ── Hook errors ───────────────────────────────────────────────────────────


def test_unknown_hook_rejected():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(hooks=["on_smoke"]))
    assert any("not a known hook" in e for e in exc_info.value.errors)


def test_hooks_must_be_list():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(hooks="on_enable"))
    assert any("hooks must be a list" in e for e in exc_info.value.errors)


# ── Structural errors ─────────────────────────────────────────────────────


def test_empty_dict_rejected():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest({})
    assert len(exc_info.value.errors) >= 4  # id, name, version, oxs_version, entry_point


def test_none_rejected():
    with pytest.raises(OXSManifestError):
        validate_manifest(None)


def test_string_rejected():
    with pytest.raises(OXSManifestError):
        validate_manifest("not a manifest")


def test_invalid_json_string():
    with pytest.raises(OXSManifestError):
        validate_manifest("{not valid json")


def test_invalid_json_bytes():
    with pytest.raises(OXSManifestError):
        validate_manifest(b"\xff\xfe\xfd not valid utf-8")


def test_settings_schema_must_be_dict():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(settings_schema="not a schema"))
    assert any("settings_schema" in e for e in exc_info.value.errors)


def test_data_schema_must_be_dict():
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest(_minimal_manifest(data_schema="not a schema"))
    assert any("data_schema" in e for e in exc_info.value.errors)


def test_author_must_be_object():
    with pytest.raises(OXSManifestError):
        validate_manifest(_minimal_manifest(author="alice"))


def test_author_name_required():
    with pytest.raises(OXSManifestError):
        validate_manifest(_minimal_manifest(author={"email": "x@y.z"}))


def test_python_deps_must_be_list_of_strings():
    with pytest.raises(OXSManifestError):
        validate_manifest(_minimal_manifest(python_deps=[1, 2, 3]))


def test_repository_must_have_scheme():
    m = _minimal_manifest(repository="github.com/alice/extension")
    with pytest.raises(OXSManifestError):
        validate_manifest(m)


def test_license_must_be_string():
    with pytest.raises(OXSManifestError):
        validate_manifest(_minimal_manifest(license=123))


# ── Multi-error reporting ─────────────────────────────────────────────────


def test_multiple_errors_reported_at_once():
    """Validate that we don't fail on the first error — we collect all."""
    with pytest.raises(OXSManifestError) as exc_info:
        validate_manifest({
            "id": "Has Spaces",
            "name": "",
            "version": "not-a-version",
            "oxs_version": "not a range",
            "entry_point": "garbage",
        })
    errs = exc_info.value.errors
    # At least one error per missing/invalid top-level field
    assert len(errs) >= 4