import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "scripts" / "migrate_searxng_settings.py"
COMPOSE_FILES = (
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.gpu-amd.yml",
    ROOT / "docker-compose.gpu-nvidia.yml",
)


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MIGRATION), str(path)],
        capture_output=True,
        check=False,
        text=True,
    )


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("searxng_settings_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retained_settings_gain_defaults_without_changing_custom_content(tmp_path):
    settings = tmp_path / "settings.yml"
    retained = (
        b"# retained deployment settings\n"
        b"server:\n"
        b'  secret_key: "representative-retained-secret"\n'
        b"search:\n"
        b"  safe_search: 1\n"
        b"  formats:\n"
        b"    - html\n"
        b"    - json\n"
        b"ui:\n"
        b"  static_use_hash: true\n"
    )
    settings.write_bytes(retained)
    settings.chmod(0o640)
    before = settings.stat()

    first = _run(settings)

    assert first.returncode == 0, first.stderr
    assert "representative-retained-secret" not in first.stdout + first.stderr
    assert settings.read_bytes() == (
        b"# retained deployment settings\n"
        b"use_default_settings: true\n"
        + retained.removeprefix(b"# retained deployment settings\n")
    )
    after = settings.stat()
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)

    migrated = settings.read_bytes()
    second = _run(settings)

    assert second.returncode == 0, second.stderr
    assert settings.read_bytes() == migrated
    assert second.stdout == ""


def test_fresh_generated_settings_are_left_byte_identical(tmp_path):
    settings = tmp_path / "settings.yml"
    generated = (ROOT / "config" / "searxng" / "settings.yml").read_bytes().replace(
        b"__SEARXNG_SECRET__", b"representative-generated-secret"
    )
    settings.write_bytes(generated)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == generated
    assert result.stdout == ""


@pytest.mark.parametrize(
    "key",
    (
        b"use_default_settings: false\n",
        b"use_default_settings:\n  engines:\n    keep_only:\n      - brave\n",
        b"'use_default_settings': true\n",
        b'"use_default_settings": true\n',
    ),
)
def test_explicit_top_level_setting_is_not_overridden(tmp_path, key):
    settings = tmp_path / "settings.yml"
    original = key + b"server:\n  secret_key: retained\n"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == original


def test_key_is_inserted_inside_explicit_yaml_document(tmp_path):
    settings = tmp_path / "settings.yml"
    original = b"\xef\xbb\xbf# header\r\n---\r\nserver:\r\n  secret_key: retained\r\n"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == (
        b"\xef\xbb\xbf# header\r\n---\r\nuse_default_settings: true\r\n"
        b"server:\r\n  secret_key: retained\r\n"
    )


def test_indented_root_mapping_keeps_its_existing_indent(tmp_path):
    settings = tmp_path / "settings.yml"
    original = b"  server:\n    secret_key: retained\n  search:\n    safe_search: 1\n"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == b"  use_default_settings: true\n" + original


@pytest.mark.parametrize(
    "property_line",
    (b"!!map\n", b"&settings\n", b"--- !!map\n"),
)
def test_block_mapping_properties_stay_attached_to_the_root(tmp_path, property_line):
    settings = tmp_path / "settings.yml"
    mapping = b"server:\n  secret_key: retained\nsearch:\n  safe_search: 1\n"
    original = property_line + mapping
    settings.write_bytes(original)
    original_data = yaml.safe_load(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    expected = property_line + b"use_default_settings: true\n" + mapping
    assert settings.read_bytes() == expected
    migrated_data = yaml.safe_load(settings.read_bytes())
    assert migrated_data.pop("use_default_settings") is True
    assert migrated_data == original_data


def test_flow_mapping_gains_default_inheritance_without_reformatting(tmp_path):
    settings = tmp_path / "settings.yml"
    original = b"{server: {secret_key: retained}, search: {safe_search: 1}}\n"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == (
        b"{use_default_settings: true, " + original.removeprefix(b"{")
    )


@pytest.mark.parametrize(
    ("original", "expected"),
    (
        (b"{}\n", b"{use_default_settings: true}\n"),
        (
            b"{server: {use_default_settings: false, secret_key: retained}}\n",
            b"{use_default_settings: true, "
            b"server: {use_default_settings: false, secret_key: retained}}\n",
        ),
        (
            b"!!map {server: {secret_key: retained}}\n",
            b"!!map {use_default_settings: true, server: {secret_key: retained}}\n",
        ),
    ),
)
def test_other_flow_mapping_shapes_gain_only_the_root_key(tmp_path, original, expected):
    settings = tmp_path / "settings.yml"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == expected


@pytest.mark.parametrize(
    "original",
    (
        b"{use_default_settings: true, server: {secret_key: retained}}\n",
        b'{"use_default_settings": {engines: {keep_only: [brave]}}}\n',
        b"{use_default_settings: true, server: {secret_key: abc#def}}\n",
        b"{use_default_settings: true, server: {secret_key: 'ab''cd'}}\n",
    ),
)
def test_flow_mapping_with_existing_setting_is_left_untouched(tmp_path, original):
    settings = tmp_path / "settings.yml"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == original


@pytest.mark.parametrize(
    "original",
    (
        b"%YAML 1.1\n---\nuse_default_settings: true\n"
        b"server:\n  secret_key: retained\n",
        b"use_default_settings: true\nserver:\n  secret_key: retained\n...\n",
    ),
)
def test_valid_document_metadata_with_existing_key_is_left_untouched(
    tmp_path, original
):
    settings = tmp_path / "settings.yml"
    settings.write_bytes(original)

    result = _run(settings)

    assert result.returncode == 0, result.stderr
    assert settings.read_bytes() == original


def test_invalid_utf8_is_not_replaced(tmp_path):
    settings = tmp_path / "settings.yml"
    original = b"server:\n  secret_key: \xff\n"
    settings.write_bytes(original)
    before = os.stat(settings)

    result = _run(settings)

    after = os.stat(settings)
    assert result.returncode == 1
    assert settings.read_bytes() == original
    assert after.st_ino == before.st_ino


def test_temporary_file_is_chmodded_before_it_is_chowned(tmp_path, monkeypatch):
    # The Compose cap set is `cap_drop: ALL` plus CHOWN/SETGID/SETUID/
    # DAC_OVERRIDE and carries no FOWNER, and searxng's entrypoint chowns
    # /etc/searxng to searxng:searxng, so every retained settings file is owned
    # by that user. Chowning the temporary file first therefore makes the chmod
    # that follows fail with EPERM, and `set -eu` in the Compose entrypoint
    # turns that into a container that never starts. The guard below refuses
    # the chmod once the chown has landed, the way the kernel does.
    migration = _load_migration_module()
    settings = tmp_path / "settings.yml"
    settings.write_bytes(b"server:\n  secret_key: retained\n")
    settings.chmod(0o640)
    calls = []
    real_fchmod = migration.os.fchmod
    real_fchown = migration.os.fchown

    def guarded_fchmod(fd, mode):
        if "fchown" in calls:
            raise PermissionError(1, "Operation not permitted")
        calls.append("fchmod")
        return real_fchmod(fd, mode)

    def recording_fchown(fd, uid, gid):
        calls.append("fchown")
        return real_fchown(fd, uid, gid)

    monkeypatch.setattr(migration.os, "fchmod", guarded_fchmod)
    monkeypatch.setattr(migration.os, "fchown", recording_fchown)

    assert migration.migrate_settings(settings) is True

    assert calls == ["fchmod", "fchown"]
    assert stat.S_IMODE(settings.stat().st_mode) == 0o640
    assert settings.read_bytes() == (
        b"use_default_settings: true\nserver:\n  secret_key: retained\n"
    )


def test_replace_failure_preserves_original_and_removes_temporary_file(
    tmp_path, monkeypatch
):
    migration = _load_migration_module()
    settings = tmp_path / "settings.yml"
    original = b"server:\n  secret_key: retained\n"
    settings.write_bytes(original)
    before = settings.stat()

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(migration.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        migration.migrate_settings(settings)

    after = settings.stat()
    assert settings.read_bytes() == original
    assert after.st_ino == before.st_ino
    assert list(tmp_path.iterdir()) == [settings]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES, ids=lambda path: path.name)
def test_compose_runs_migration_for_all_variants(compose_file):
    text = compose_file.read_text(encoding="utf-8")

    # The `|| true` is load-bearing: the entrypoint runs under `set -eu`, so
    # without it a settings file the migration cannot parse or rewrite stops
    # searxng from starting at all instead of merely going unmigrated.
    assert (
        "/usr/local/searxng/.venv/bin/python /tmp/migrate-searxng-settings.py "
        "/etc/searxng/settings.yml || true" in text
    )
    assert (
        "./scripts/migrate_searxng_settings.py:"
        "/tmp/migrate-searxng-settings.py:ro,z" in text
    )
