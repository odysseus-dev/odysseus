"""Tests for the fail-closed artifact verification step (.github/scripts/verify_sanitized_artifacts.py).

Validates that:
1. Safe JUnit XML reporting is preserved across all scenarios.
2. Abnormal runner termination without verified disposable CI fixture purges all diagnostics.
3. Valid disposable CI environments sanitize trace archives fail-closed.
4. Malformed trace archives are deleted fail-closed without corrupting or deleting JUnit XML reports.
5. In-progress temporary files, HAR files, and database dumps are purged unconditionally.
6. Top-level exceptions fail closed to purge all non-XML diagnostics.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / ".github" / "scripts" / "verify_sanitized_artifacts.py"

import importlib.util

spec = importlib.util.spec_from_file_location("verify_sanitized_artifacts", str(VERIFY_SCRIPT))
verify_module = importlib.util.module_from_spec(spec)
sys.modules["verify_sanitized_artifacts"] = verify_module
spec.loader.exec_module(verify_module)

is_valid_disposable_ci_fixture = verify_module.is_valid_disposable_ci_fixture
verify_and_sanitize_artifacts = verify_module.verify_and_sanitize_artifacts


def _setup_disposable_ci(tmp_path: Path) -> dict[str, str]:
    data_dir = tmp_path / "odysseus-photo-editor-e2e-fixture"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "app.db"
    db_path.write_bytes(b"SQLite format 3\x00")
    marker_file = data_dir / ".disposable-ci-fixture.json"
    marker_file.write_text(
        json.dumps({
            "synthetic": True,
            "authenticated": True,
            "database": str(db_path),
        })
    )
    return {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "ODYSSEUS_E2E_DISPOSABLE_ARTIFACTS": "1",
        "PHOTO_EDITOR_E2E_DATA_DIR": str(data_dir),
        "PHOTO_EDITOR_E2E_DB_PATH": str(db_path),
        "PHOTO_EDITOR_E2E_AUTH": "1",
        "ODYSSEUS_TEST_PYTHON": sys.executable,
    }


def test_preserves_valid_junit_xml_cleanly(tmp_path):
    """Valid JUnit XML files are preserved intact."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="137" failures="0"/></testsuites>')

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True
    assert junit.exists()
    assert "tests=\"137\"" in junit.read_text()


def test_fail_closed_abnormal_termination_purges_all_diagnostics(tmp_path, monkeypatch):
    """Abnormal runner termination without disposable fixture purges all diagnostics, keeping JUnit."""
    # Simulate CI without disposable fixture
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("ODYSSEUS_E2E_DISPOSABLE_ARTIFACTS", raising=False)
    monkeypatch.delenv("PHOTO_EDITOR_E2E_DATA_DIR", raising=False)

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="10" failures="1"/></testsuites>')

    trace = results_dir / "trace.zip"
    trace.write_bytes(b"PK0304 sensitive trace archive")
    har = results_dir / "session.har"
    har.write_text('{"log": {"entries": ["secret"]}}')
    png = results_dir / "failure.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake screenshot")
    tmp = results_dir / "temp.tmp"
    tmp.write_text("in-progress trace")
    db = results_dir / "app.db"
    db.write_bytes(b"SQLite format 3 database")

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True

    # Safe JUnit reporting MUST be preserved
    assert junit.exists()
    assert "failures=\"1\"" in junit.read_text()

    # All sensitive diagnostics MUST be purged
    assert not trace.exists()
    assert not har.exists()
    assert not png.exists()
    assert not tmp.exists()
    assert not db.exists()


def test_sanitizes_trace_archive_when_disposable_ci_verified(tmp_path, monkeypatch):
    """When running inside a verified disposable CI fixture, trace archives are sanitized fail-closed."""
    env = _setup_disposable_ci(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="137" failures="0"/></testsuites>')

    trace = results_dir / "trace.zip"
    with zipfile.ZipFile(trace, "w") as z:
        action = {"type": "action", "name": "fill", "params": {"value": "super_secret_password"}}
        z.writestr("0-trace.trace", json.dumps(action).encode() + b"\n")

    png = results_dir / "failure.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nvalid screenshot payload")

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True

    assert junit.exists()
    assert trace.exists()
    assert png.exists()

    with zipfile.ZipFile(trace, "r") as z:
        content = json.loads(z.read("0-trace.trace").decode().strip())
        assert content["params"]["value"] == "[REDACTED]"


def test_malformed_trace_archive_purged_preserving_junit_xml(tmp_path, monkeypatch):
    """Malformed trace archive is purged fail-closed without affecting JUnit XML."""
    env = _setup_disposable_ci(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="137" failures="1"/></testsuites>')

    # Create trace archive with malformed JSONL line
    malformed_trace = results_dir / "trace.zip"
    with zipfile.ZipFile(malformed_trace, "w") as z:
        z.writestr("0-trace.trace", b'{"type": "action"}\nmalformed not json line\n')

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True

    # JUnit XML is preserved
    assert junit.exists()
    # Malformed archive is purged!
    assert not malformed_trace.exists()


def test_unhandled_exception_fails_closed_purges_all_non_xml(tmp_path, monkeypatch):
    """Unexpected exception during verification triggers emergency fail-closed purge of non-XML files."""
    env = _setup_disposable_ci(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="137" failures="0"/></testsuites>')

    trace = results_dir / "trace.zip"
    trace.write_bytes(b"PK0304 trace")

    def broken_sanitize(zp):
        raise RuntimeError("Simulated unexpected crash in sanitizer")

    monkeypatch.setattr(verify_module, "sanitize_trace_archive", broken_sanitize)

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is False
    # Safe JUnit reporting is preserved
    assert junit.exists()
    # Sensitive non-XML file is purged fail-closed!
    assert not trace.exists()


def test_cli_invocation_returns_cleanly(tmp_path):
    """Running the script as a CLI entrypoint succeeds with exit code 0."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "junit.xml"
    junit.write_text('<testsuites><testsuite name="native-node" tests="43" failures="0"/></testsuites>')

    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(results_dir)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert junit.exists()
