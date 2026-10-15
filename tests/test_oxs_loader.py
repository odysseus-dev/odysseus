"""Tests for OXS loader (directory + entry-point discovery)."""

from __future__ import annotations

import json
import os

import pytest

from core.extensions import (
    OXSManifestError,
    OXSSecurityError,
    discover_all,
    verify_discovered,
)


def _write_minimal(root: str, ext_id: str = "my-ext", **overrides) -> str:
    sub = os.path.join(root, ext_id)
    os.makedirs(sub, exist_ok=True)
    manifest = {
        "id": ext_id,
        "name": ext_id,
        "version": "1.0.0",
        "oxs_version": ">=1.0.0",
        "entry_point": "plugin:register",
        "capabilities": {},
        "file_hashes": {},
    }
    manifest.update(overrides)
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)
    return sub


# ── Directory scan ─────────────────────────────────────────────────────────


def test_discover_bundled(tmp_path):
    bundled = str(tmp_path / "bundled")
    _write_minimal(bundled, "ext-a")
    found = discover_all(bundled_dir=bundled, user_dir=str(tmp_path / "user"),
                         include_entry_points=False)
    assert [e.extension_id for e in found] == ["ext-a"]
    assert found[0].source == "bundled"


def test_discover_user(tmp_path):
    user = str(tmp_path / "user")
    _write_minimal(user, "ext-b")
    found = discover_all(bundled_dir=str(tmp_path / "no-bundled"), user_dir=user,
                         include_entry_points=False)
    assert [e.extension_id for e in found] == ["ext-b"]
    assert found[0].source == "user"


def test_discover_returns_sorted(tmp_path):
    bundled = str(tmp_path / "bundled")
    for ext_id in ("zebra", "alpha", "mango"):
        _write_minimal(bundled, ext_id)
    found = discover_all(bundled_dir=bundled, user_dir=str(tmp_path / "user"),
                         include_entry_points=False)
    assert [e.extension_id for e in found] == ["alpha", "mango", "zebra"]


def test_discover_skips_dir_without_manifest(tmp_path):
    bundled = str(tmp_path / "bundled")
    os.makedirs(os.path.join(bundled, "no-manifest"), exist_ok=True)
    _write_minimal(bundled, "with-manifest")
    found = discover_all(bundled_dir=bundled, user_dir=str(tmp_path / "user"),
                         include_entry_points=False)
    assert [e.extension_id for e in found] == ["with-manifest"]


def test_discover_skips_invalid_manifest(tmp_path):
    bundled = str(tmp_path / "bundled")
    bad = os.path.join(bundled, "bad")
    os.makedirs(bad)
    with open(os.path.join(bad, "extension.json"), "w") as f:
        # Missing required fields
        json.dump({"name": "Bad"}, f)
    _write_minimal(bundled, "good")
    found = discover_all(bundled_dir=bundled, user_dir=str(tmp_path / "user"),
                         include_entry_points=False)
    assert [e.extension_id for e in found] == ["good"]


def test_discover_returns_empty_when_dirs_missing(tmp_path):
    found = discover_all(
        bundled_dir=str(tmp_path / "no-bundled"),
        user_dir=str(tmp_path / "no-user"),
        include_entry_points=False,
    )
    assert found == []


def test_discover_dedupes_bundled_wins(tmp_path):
    bundled = str(tmp_path / "bundled")
    user = str(tmp_path / "user")
    _write_minimal(bundled, "shared")
    _write_minimal(user, "shared")
    found = discover_all(bundled_dir=bundled, user_dir=user,
                         include_entry_points=False)
    assert len(found) == 1
    assert found[0].source == "bundled"


# ── Hash verification ──────────────────────────────────────────────────────


def test_verify_discovered_skips_bundled(tmp_path):
    bundled = str(tmp_path / "bundled")
    _write_minimal(bundled, "ext")
    found = discover_all(bundled_dir=bundled, user_dir=str(tmp_path / "user"),
                         include_entry_points=False)
    # No file_hashes declared; bundled should pass anyway.
    verify_discovered(found[0])


def test_verify_discovered_passes_when_hashes_match(tmp_path):
    user = str(tmp_path / "user")
    sub = _write_minimal(user, "ext")
    # Add a real file to the extension
    plugin_path = os.path.join(sub, "plugin.py")
    with open(plugin_path, "w") as f:
        f.write("def register(host): pass")
    # Compute the actual hash of plugin.py
    from core.extensions import sha256_of_file
    h = sha256_of_file(plugin_path)
    # Update the manifest's file_hashes to include plugin.py
    with open(os.path.join(sub, "extension.json")) as f:
        manifest = json.load(f)
    manifest["file_hashes"] = {"plugin.py": h}
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)

    found = discover_all(bundled_dir=str(tmp_path / "no-bundled"), user_dir=user,
                         include_entry_points=False)
    verify_discovered(found[0])  # should not raise


def test_verify_discovered_rejects_hash_mismatch(tmp_path):
    user = str(tmp_path / "user")
    sub = _write_minimal(user, "ext")
    with open(os.path.join(sub, "extension.json")) as f:
        manifest = json.load(f)
    manifest["file_hashes"] = {"extension.json": "sha256:" + "0" * 64}
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)

    found = discover_all(bundled_dir=str(tmp_path / "no-bundled"), user_dir=user,
                         include_entry_points=False)
    with pytest.raises(OXSSecurityError) as exc:
        verify_discovered(found[0])
    assert any("hash mismatch" in e for e in exc.value.errors)


def test_verify_discovered_rejects_missing_file(tmp_path):
    user = str(tmp_path / "user")
    _write_minimal(user, "ext")
    # Declare a hash for a file that does not exist
    sub = os.path.join(user, "ext")
    with open(os.path.join(sub, "extension.json")) as f:
        manifest = json.load(f)
    manifest["file_hashes"] = {"plugin.py": "sha256:" + "a" * 64}
    with open(os.path.join(sub, "extension.json"), "w") as f:
        json.dump(manifest, f)

    found = discover_all(bundled_dir=str(tmp_path / "no-bundled"), user_dir=user,
                         include_entry_points=False)
    with pytest.raises(OXSSecurityError) as exc:
        verify_discovered(found[0])
    assert any("missing on disk" in e for e in exc.value.errors)


# ── Bundled discovery of hello-odysseus (regression) ───────────────────────


def test_real_hello_odysseus_is_discoverable():
    """The reference extension shipped in extensions/ must be found."""
    import os
    bundled = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "extensions",
    ))
    found = discover_all(bundled_dir=bundled, user_dir="/tmp/__no_user__",
                         include_entry_points=False)
    ids = [e.extension_id for e in found]
    assert "hello-odysseus" in ids


def test_real_hello_odysseus_passes_verify():
    """The reference extension's hashes must match exactly."""
    import os
    bundled = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "extensions",
    ))
    found = discover_all(bundled_dir=bundled, user_dir="/tmp/__no_user__",
                         include_entry_points=False)
    hello = next(e for e in found if e.extension_id == "hello-odysseus")
    verify_discovered(hello)