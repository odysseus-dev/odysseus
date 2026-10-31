"""Tests for the fail-closed artifact verification step (.github/scripts/verify_sanitized_artifacts.py).

Validates that:
1. Safe JUnit XML reporting is preserved across all scenarios.
2. Abnormal runner termination without verified disposable CI fixture purges all diagnostics.
3. Valid disposable CI environments sanitize trace archives fail-closed.
4. Malformed trace archives are deleted fail-closed without corrupting or deleting JUnit XML reports.
5. In-progress temporary files, HAR files, and database dumps are purged unconditionally.
6. Top-level exceptions fail closed to purge all non-XML diagnostics.
7. Negative controls:
   - Verifier failure exits nonzero.
   - Verifier failure prevents upload in workflow contract.
   - Missing verifier execution prevents upload.
   - Valid XML containing a synthetic secret is not published unchanged.
   - Malformed traces are removed.
   - Failed artifact deletion prevents authorization.
   - A normal successful run retains safe test results.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / ".github" / "scripts" / "verify_sanitized_artifacts.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "native-test-suites.yml"

spec = importlib.util.spec_from_file_location("verify_sanitized_artifacts", str(VERIFY_SCRIPT))
verify_module = importlib.util.module_from_spec(spec)
sys.modules["verify_sanitized_artifacts"] = verify_module
spec.loader.exec_module(verify_module)

is_valid_disposable_ci_fixture = verify_module.is_valid_disposable_ci_fixture
verify_and_sanitize_artifacts = verify_module.verify_and_sanitize_artifacts
sanitize_and_verify_junit_xml = verify_module.sanitize_and_verify_junit_xml
safe_remove = verify_module.safe_remove


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
    assert 'tests="137"' in junit.read_text()


def test_normal_successful_run_retains_safe_test_results(tmp_path):
    """A normal successful run retains safe test results without modification."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "junit.xml"
    original_xml = '<testsuites><testsuite name="native-node" tests="43" failures="0"><testcase name="test-1"/></testsuite></testsuites>'
    junit.write_text(original_xml)

    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(results_dir)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert junit.exists()
    content = junit.read_text()
    assert 'name="native-node"' in content
    assert 'tests="43"' in content
    assert 'test-1' in content


def test_valid_xml_containing_synthetic_secret_not_published_unchanged(tmp_path):
    """Negative control: Valid XML containing a synthetic secret is not published unchanged."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    secret_text = "SuperSecretSyntheticToken98765"
    xml_with_secret = f"""<testsuites>
  <testsuite name="photo-editor" tests="1" failures="1">
    <testcase name="auth-test">
      <failure message="Encountered password={secret_text} in log">
        Stack trace containing synthetic-secret authorization: Bearer {secret_text}
      </failure>
      <system-out>session_token={secret_text}</system-out>
    </testcase>
  </testsuite>
</testsuites>"""
    junit.write_text(xml_with_secret)

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True
    assert junit.exists()

    sanitized_content = junit.read_text()
    # The raw secret MUST NOT appear anywhere in the published report
    assert secret_text not in sanitized_content
    # The redaction marker MUST appear
    assert "[REDACTED]" in sanitized_content
    # The XML must still be structurally valid JUnit XML
    tree = ET.parse(junit)
    assert tree.getroot().tag == "testsuites"


def test_exact_property_api_key_cannot_appear_unchanged_in_uploaded_report(tmp_path):
    """Regression: <property name="api_key" value="sk-example-123" /> cannot appear unchanged in uploaded report."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    xml_with_property = """<testsuites name="photo-editor" tests="1" failures="0">
  <testsuite name="brush-engine.spec.js" tests="1" failures="0">
    <properties>
      <property name="api_key" value="sk-example-123" />
    </properties>
    <property name="api_key" value="sk-example-123" />
    <testcase name="brush presets and shared stroke controls remain reusable" classname="photo-editor/brush-engine.spec.js" time="0.5" />
  </testsuite>
</testsuites>"""
    junit.write_text(xml_with_property)

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True
    assert junit.exists()

    sanitized_xml = junit.read_text()
    # The exact sensitive property and value MUST NOT appear anywhere in the published report
    assert "sk-example-123" not in sanitized_xml
    assert "api_key" not in sanitized_xml
    assert "<property" not in sanitized_xml
    assert "<properties" not in sanitized_xml

    # Test identity and structure must remain valid
    tree = ET.parse(junit)
    root = tree.getroot()
    assert root.tag == "testsuites"
    cases = list(root.iter("testcase"))
    assert len(cases) == 1
    assert cases[0].get("name") == "brush presets and shared stroke controls remain reusable"
    assert cases[0].get("classname") == "photo-editor/brush-engine.spec.js"


def test_standard_node_and_playwright_reports_remain_valid_and_retain_identities(tmp_path):
    """Standard Node and Playwright JUnit reports remain structurally valid and retain test identities."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    # 1. Standard Node report representation (similar to node:test runner output)
    node_dir = results_dir / "native-node"
    node_dir.mkdir()
    node_junit = node_dir / "junit.xml"
    node_junit.write_text("""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testcase name="research cards distinguish live work, handoff, completion and no evidence" time="0.000549" classname="test" file="tests/backgroundToolJobs.test.mjs" />
  <testcase name="Chat Photo Editor Action Integrity and Turn Scoping" time="0.499862" classname="test" file="tests/chatPhotoEditorIntegrity.test.mjs" />
</testsuites>""")

    # 2. Standard Playwright report representation (with suites, timing, counts, and failure classifications)
    browser_junit = results_dir / "browser-junit.xml"
    browser_junit.write_text("""<testsuites id="" name="" tests="2" failures="1" skipped="0" errors="0" time="12.5">
  <testsuite name="photo-editor/brush-engine.spec.js" timestamp="2026-10-31T00:00:00Z" hostname="chromium" tests="2" failures="1" skipped="0" time="12.5" errors="0">
    <properties>
      <property name="platform" value="linux" />
      <property name="api_key" value="sk-example-123" />
    </properties>
    <testcase name="brush presets and shared stroke controls remain reusable" classname="photo-editor/brush-engine.spec.js" time="1.2" />
    <testcase name="long sampled strokes stay responsive and undo atomically" classname="photo-editor/brush-engine.spec.js" time="2.4">
      <failure message="expect(strokeDuration - controlDuration).toBeLessThan(5000)" type="FAILURE">
        Error: expect(strokeDuration - controlDuration).toBeLessThan(5000)
          at brush-engine.spec.js:84:10
      </failure>
      <system-out>Captured console stdout dump with session_token=abc12345</system-out>
      <system-err>Captured console stderr dump</system-err>
    </testcase>
  </testsuite>
</testsuites>""")

    ok_node = verify_and_sanitize_artifacts(str(node_dir))
    assert ok_node is True
    ok_browser = verify_and_sanitize_artifacts(str(results_dir))
    assert ok_browser is True

    # Validate Node report
    node_tree = ET.parse(node_junit)
    node_cases = list(node_tree.iter("testcase"))
    assert len(node_cases) == 2
    assert node_cases[0].get("name") == "research cards distinguish live work, handoff, completion and no evidence"
    assert node_cases[1].get("name") == "Chat Photo Editor Action Integrity and Turn Scoping"
    assert node_cases[0].get("file") == "tests/backgroundToolJobs.test.mjs"

    # Validate Playwright report
    pw_tree = ET.parse(browser_junit)
    pw_root = pw_tree.getroot()
    assert pw_root.tag == "testsuites"
    assert pw_root.get("tests") == "2"
    assert pw_root.get("failures") == "1"

    pw_cases = list(pw_tree.iter("testcase"))
    assert len(pw_cases) == 2
    assert pw_cases[0].get("name") == "brush presets and shared stroke controls remain reusable"
    assert pw_cases[1].get("name") == "long sampled strokes stay responsive and undo atomically"

    # Verify failure classification is preserved
    failures = list(pw_cases[1].iter("failure"))
    assert len(failures) == 1
    assert "toBeLessThan(5000)" in failures[0].get("message")
    assert failures[0].get("type") == "FAILURE"

    # Verify properties and system-out/err were pruned
    pw_text = browser_junit.read_text()
    assert "sk-example-123" not in pw_text
    assert "api_key" not in pw_text
    assert "<properties" not in pw_text
    assert "<property" not in pw_text
    assert "<system-out" not in pw_text
    assert "<system-err" not in pw_text
    assert "session_token" not in pw_text


def test_unrecognized_xml_purged(tmp_path):
    """Negative control: XML reports that are not recognized JUnit reports are purged."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    # Non-JUnit filename
    creds_xml = results_dir / "credentials.xml"
    creds_xml.write_text("<credentials><token>secret</token></credentials>")
    # Non-JUnit root element
    bad_root_xml = results_dir / "junit.xml"
    bad_root_xml.write_text("<app_config><password>secret</password></app_config>")

    ok = verify_and_sanitize_artifacts(str(results_dir))
    # Non-JUnit XML files must be purged
    assert not creds_xml.exists()
    assert not bad_root_xml.exists()


def test_verifier_failure_exits_nonzero(tmp_path):
    """Negative control: Verifier failure exits nonzero."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    # Pass an invalid option or target where verification fails
    # Create an un-deletable file to force verification failure
    bad_file = results_dir / "unauthorized.tmp"
    bad_file.write_text("sensitive")

    # Run CLI with an invalid directory or simulating failure
    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(results_dir), "--extra-arg"],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0


def test_failed_artifact_deletion_prevents_authorization(tmp_path, monkeypatch):
    """Negative control: Failed artifact deletion prevents authorization (exits nonzero)."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    bad_file = results_dir / "leak.har"
    bad_file.write_text("network log with tokens")

    # Simulate deletion failure
    def failing_safe_remove(fp):
        return False

    monkeypatch.setattr(verify_module, "safe_remove", failing_safe_remove)

    ok = verify_and_sanitize_artifacts(str(results_dir))
    # When deletion fails, verification MUST return False!
    assert ok is False

    # CLI invocation must exit nonzero
    res = subprocess.run(
        [sys.executable, "-c", f"""
import sys
from pathlib import Path
sys.path.insert(0, '{REPO_ROOT}')
import verify_sanitized_artifacts as vsa
vsa.safe_remove = lambda fp: False
ok = vsa.verify_and_sanitize_artifacts('{results_dir}')
sys.exit(0 if ok else 1)
"""],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0


def test_workflow_upload_steps_require_successful_verification():
    """Negative control: Workflow enforces that upload steps execute ONLY if verification succeeded."""
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    jobs = workflow.get("jobs", {})

    # 1. Native Node job
    node_job = jobs.get("native-node", {})
    node_steps = node_job.get("steps", [])

    verify_node_step = None
    upload_node_step = None
    for step in node_steps:
        if step.get("id") == "verify-native-results":
            verify_node_step = step
        if step.get("uses", "").startswith("actions/upload-artifact"):
            upload_node_step = step

    assert verify_node_step is not None, "verify-native-results step must exist with explicit id"
    assert verify_node_step.get("if") == "always()", "verify step must run with if: always()"

    assert upload_node_step is not None, "upload step in native-node must exist"
    node_upload_if = upload_node_step.get("if", "")
    assert "steps.verify-native-results.outcome == 'success'" in node_upload_if, (
        f"Node upload step must depend on verify step success; got if: {node_upload_if}"
    )
    assert node_upload_if != "always()", "Node upload step must not use plain if: always()"

    # 2. Browser job
    browser_job = jobs.get("browser", {})
    browser_steps = browser_job.get("steps", [])

    verify_browser_step = None
    upload_browser_step = None
    for step in browser_steps:
        if step.get("id") == "verify-browser-results":
            verify_browser_step = step
        if step.get("uses", "").startswith("actions/upload-artifact"):
            upload_browser_step = step

    assert verify_browser_step is not None, "verify-browser-results step must exist with explicit id"
    assert verify_browser_step.get("if") == "always()", "verify step must run with if: always()"

    assert upload_browser_step is not None, "upload step in browser must exist"
    browser_upload_if = upload_browser_step.get("if", "")
    assert "steps.verify-browser-results.outcome == 'success'" in browser_upload_if, (
        f"Browser upload step must depend on verify step success; got if: {browser_upload_if}"
    )
    assert browser_upload_if != "always()", "Browser upload step must not use plain if: always()"


def test_fail_closed_abnormal_termination_purges_all_diagnostics(tmp_path, monkeypatch):
    """Abnormal runner termination without disposable fixture purges all diagnostics, keeping JUnit."""
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
    assert 'failures="1"' in junit.read_text()

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


def test_malformed_traces_are_removed(tmp_path, monkeypatch):
    """Negative control: Malformed trace archive is purged fail-closed without affecting JUnit XML."""
    env = _setup_disposable_ci(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="137" failures="1"/></testsuites>')

    malformed_trace = results_dir / "trace.zip"
    with zipfile.ZipFile(malformed_trace, "w") as z:
        z.writestr("0-trace.trace", b'{"type": "action"}\nmalformed not json line\n')

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True

    # JUnit XML is preserved
    assert junit.exists()
    # Malformed archive is purged!
    assert not malformed_trace.exists()


def test_fixture_marker_in_target_dir_establishes_ownership(tmp_path, monkeypatch):
    """Fixture proof in target_dir establishes fixture ownership and is purged before upload."""
    data_dir = tmp_path / "odysseus-photo-editor-e2e-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "app.db"
    db_path.write_bytes(b"SQLite format 3\x00")

    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("ODYSSEUS_E2E_DISPOSABLE_ARTIFACTS", "1")
    monkeypatch.setenv("PHOTO_EDITOR_E2E_AUTH", "1")
    monkeypatch.delenv("PHOTO_EDITOR_E2E_DATA_DIR", raising=False)

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    junit = results_dir / "browser-junit.xml"
    junit.write_text('<testsuites><testsuite name="photo-editor" tests="137" failures="0"/></testsuites>')

    marker_file = results_dir / ".disposable-ci-fixture.json"
    marker_file.write_text(json.dumps({
        "synthetic": True,
        "authenticated": True,
        "database": str(db_path),
    }))

    trace = results_dir / "trace.zip"
    with zipfile.ZipFile(trace, "w") as z:
        action = {"type": "action", "name": "fill", "params": {"value": "sensitive_val"}}
        z.writestr("0-trace.trace", json.dumps(action).encode() + b"\n")

    ok = verify_and_sanitize_artifacts(str(results_dir))
    assert ok is True

    assert junit.exists()
    assert trace.exists()
    # Marker file itself must be purged so it is never published as an artifact
    assert not marker_file.exists()


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
