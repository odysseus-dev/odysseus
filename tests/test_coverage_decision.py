"""Negative controls and policy verification for selective CI coverage.

Covers all 15 required negative control scenarios from Section 9:
1. Documentation-only changes.
2. Direct Photo Editor changes.
3. Shared frontend dependency changes.
4. Authentication and application API changes.
5. Test additions and deletions.
6. Test manifest changes.
7. Test-runner and workflow changes.
8. Unknown or unclassified paths.
9. Mixed changes across multiple areas.
10. Renamed files.
11. A required test job failing.
12. A required test job unexpectedly skipping.
13. A coverage decision job failing.
14. A permitted test skip.
15. An incomplete test inventory.
Plus: Targeted application regression prevention.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / ".github" / "scripts" / "coverage_decision.py"

# Dynamically import coverage_decision from .github/scripts/
spec = importlib.util.spec_from_file_location("coverage_decision", SCRIPT_PATH)
coverage_decision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage_decision)

classify_paths = coverage_decision.classify_paths
evaluate_gate_compliance = coverage_decision.evaluate_gate_compliance
parse_diff_paths = coverage_decision.parse_diff_paths
validate_test_manifest = coverage_decision.validate_test_manifest


def test_control_01_documentation_only_changes():
    """1. Documentation-only changes must skip native Node and Chromium suites."""
    paths = [
        "README.md",
        "docs/maintainers/README.md",
        "docs/testing-and-releases.md",
        "website/configuration-reference.md",
        "CONTRIBUTING.md",
    ]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_node is False, "Docs-only must not trigger Node suite"
    assert run_browser is False, "Docs-only must not trigger Chromium suite"
    assert "Documentation-only" in reason


def test_control_02_direct_photo_editor_changes():
    """2. Direct Photo Editor changes must trigger browser and Node validation."""
    paths = ["static/js/editor/retouch.js"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_browser is True, "Photo Editor change must trigger Chromium suite"
    assert run_node is True, "Photo Editor change must trigger Node suite"
    assert "Photo Editor" in reason

    paths_spec = ["tests/e2e/photo-editor/brush-engine.spec.js"]
    run_node, run_browser, _ = classify_paths(paths_spec)
    assert run_browser is True
    assert run_node is True


def test_control_03_shared_frontend_dependency_changes():
    """3. Shared frontend infrastructure changes must trigger comprehensive coverage."""
    paths = ["static/app.js", "static/index.html"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_node is True, "Shared frontend must trigger Node suite"
    assert run_browser is True, "Shared frontend must trigger Chromium suite"
    assert "Shared frontend" in reason


def test_control_04_authentication_and_api_changes():
    """4. Authentication, sessions, and core API changes must trigger browser validation."""
    paths = ["core/auth.py"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_node is True
    assert run_browser is True
    assert "Authentication" in reason

    paths_session = ["core/session_manager.py", "routes/auth_routes.py"]
    run_node, run_browser, _ = classify_paths(paths_session)
    assert run_node is True
    assert run_browser is True


def test_control_05_test_additions_and_deletions(tmp_path):
    """5. Adding or deleting a test must be detected by inventory validation."""
    manifest_file = ROOT / "tests" / "native-ci-manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    # Test addition: a test exists in discovered but is not in manifest
    fake_manifest = tmp_path / "manifest.json"
    corrupt_data = dict(manifest_data)
    # Remove one native test from manifest so it appears as an unassigned addition
    removed = corrupt_data["native-ci"].pop(0)
    fake_manifest.write_text(json.dumps(corrupt_data), encoding="utf-8")

    is_valid, msg, _ = validate_test_manifest(fake_manifest, ROOT)
    assert is_valid is False, "Unassigned test file must fail manifest validation"
    assert removed in msg

    # Test deletion: a test is in manifest but not discovered
    fresh_manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    corrupt_data_deleted = fresh_manifest_data
    corrupt_data_deleted["native-ci"].append("tests/nonexistentTest.test.mjs")
    fake_manifest.write_text(json.dumps(corrupt_data_deleted), encoding="utf-8")
    is_valid, msg, _ = validate_test_manifest(fake_manifest, ROOT)
    assert is_valid is False, "Nonexistent test in manifest must fail manifest validation"
    assert "nonexistentTest" in msg


def test_control_06_test_manifest_changes():
    """6. Changes to test manifest must trigger execution."""
    paths = ["tests/native-ci-manifest.json"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_node is True
    assert run_browser is True
    assert "Test runner, workflow, or manifest" in reason


def test_control_07_test_runner_and_workflow_changes():
    """7. Changes to workflows and CI scripts must trigger full execution."""
    paths = [".github/workflows/native-test-suites.yml"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_node is True
    assert run_browser is True

    paths_script = [".github/scripts/run_native_tests.mjs"]
    run_node, run_browser, _ = classify_paths(paths_script)
    assert run_node is True
    assert run_browser is True


def test_control_08_unknown_or_unclassified_paths():
    """8. Unknown or ambiguous paths must conservatively trigger full coverage."""
    paths = ["infrastructure/cloud/custom_worker.tf"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_node is True, "Unknown path must trigger Node suite fail-closed"
    assert run_browser is True, "Unknown path must trigger Chromium suite fail-closed"
    assert "Unknown or unclassified" in reason


def test_control_09_mixed_changes_across_multiple_areas():
    """9. Mixed changes across multiple areas must take the union of triggers."""
    paths = [
        "README.md",
        "docs/maintainers/README.md",
        "static/js/editor/state.js",
        "src/agent_loop.py",
    ]
    run_node, run_browser, _ = classify_paths(paths)
    assert run_node is True
    assert run_browser is True


def test_control_10_renamed_files():
    """10. Renamed files must be tracked via old and new paths in git diff."""
    diff_output = (
        "R100\tdocs/old_manual.md\tstatic/js/editor/new_editor.js\n"
        "M\tCONTRIBUTING.md\n"
    )
    parsed = parse_diff_paths(diff_output)
    assert "docs/old_manual.md" in parsed
    assert "static/js/editor/new_editor.js" in parsed

    run_node, run_browser, reason = classify_paths(parsed)
    assert run_browser is True, "Rename into editor path must trigger browser suite"
    assert run_node is True


def test_control_11_required_test_job_failing():
    """11. A required test job failing must fail the aggregate gate."""
    # When node was required but failed
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser=True,
        node_result="failure",
        browser_result="success",
    )
    assert ok is False
    assert "Required Native Node suite did not succeed" in err

    # When browser was required but failed
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser=True,
        node_result="success",
        browser_result="failure",
    )
    assert ok is False
    assert "Required Chromium browser suite did not succeed" in err


def test_control_12_required_test_job_unexpectedly_skipping():
    """12. A required test job unexpectedly skipping must fail the aggregate gate."""
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser=True,
        node_result="skipped",
        browser_result="success",
    )
    assert ok is False
    assert "Required Native Node suite did not succeed" in err


def test_control_13_coverage_decision_job_failing():
    """13. A coverage decision job failing must fail the aggregate gate."""
    ok, err = evaluate_gate_compliance(
        decision_result="failure",
        run_node=True,
        run_browser=True,
        node_result="success",
        browser_result="success",
    )
    assert ok is False
    assert "Coverage decision job did not succeed" in err


def test_control_14_permitted_test_skip():
    """14. A permitted test skip must succeed at the aggregate gate."""
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node=False,
        run_browser=False,
        node_result="skipped",
        browser_result="skipped",
    )
    assert ok is True
    assert "PASSED" in msg


# Explicit aggregate gate compliance negative controls (Items 1-10)

def test_aggregate_gate_control_01_missing_native_node_decision():
    """1. Missing Native Node decision must fail fail-closed."""
    for empty_val in ["", None]:
        ok, err = evaluate_gate_compliance(
            decision_result="success",
            run_node=empty_val,
            run_browser="true",
            node_result="skipped",
            browser_result="success",
        )
        assert ok is False
        assert "Invalid or missing Native Node decision" in err


def test_aggregate_gate_control_02_missing_chromium_decision():
    """2. Missing Chromium decision must fail fail-closed."""
    for empty_val in ["", None]:
        ok, err = evaluate_gate_compliance(
            decision_result="success",
            run_node="true",
            run_browser=empty_val,
            node_result="success",
            browser_result="skipped",
        )
        assert ok is False
        assert "Invalid or missing Chromium browser decision" in err


def test_aggregate_gate_control_03_both_decisions_missing():
    """3. Both decisions missing must fail fail-closed even if jobs skipped."""
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node="",
        run_browser="",
        node_result="skipped",
        browser_result="skipped",
    )
    assert ok is False
    assert "Invalid or missing Native Node decision" in err


def test_aggregate_gate_control_04_malformed_decision_values():
    """4. Malformed decision values must fail fail-closed."""
    malformed_inputs = ["yes", "no", "1", "0", "undefined", "null", "invalid", 123, []]
    for bad in malformed_inputs:
        ok, err = evaluate_gate_compliance(
            decision_result="success",
            run_node=bad,
            run_browser="true",
            node_result="success",
            browser_result="success",
        )
        assert ok is False
        assert "Invalid or missing Native Node decision" in err

        ok, err = evaluate_gate_compliance(
            decision_result="success",
            run_node="true",
            run_browser=bad,
            node_result="success",
            browser_result="success",
        )
        assert ok is False
        assert "Invalid or missing Chromium browser decision" in err


def test_aggregate_gate_control_05_required_suite_skipped():
    """5. Required suite skipped must fail aggregate gate."""
    # Native node required but skipped
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser=False,
        node_result="skipped",
        browser_result="skipped",
    )
    assert ok is False
    assert "Required Native Node suite did not succeed" in err

    # Chromium required but skipped
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=False,
        run_browser="true",
        node_result="skipped",
        browser_result="skipped",
    )
    assert ok is False
    assert "Required Chromium browser suite did not succeed" in err


def test_aggregate_gate_control_06_required_suite_failed():
    """6. Required suite failed must fail aggregate gate."""
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node="true",
        run_browser="false",
        node_result="failure",
        browser_result="skipped",
    )
    assert ok is False
    assert "Required Native Node suite did not succeed" in err

    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=False,
        run_browser=True,
        node_result="skipped",
        browser_result="failure",
    )
    assert ok is False
    assert "Required Chromium browser suite did not succeed" in err


def test_aggregate_gate_control_07_required_suite_cancelled():
    """7. Required suite cancelled must fail aggregate gate."""
    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser=True,
        node_result="cancelled",
        browser_result="success",
    )
    assert ok is False
    assert "Required Native Node suite did not succeed" in err

    ok, err = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser=True,
        node_result="success",
        browser_result="cancelled",
    )
    assert ok is False
    assert "Required Chromium browser suite did not succeed" in err


def test_aggregate_gate_control_08_valid_authorized_skips():
    """8. Valid authorized skips must pass aggregate gate."""
    # Both skipped legitimately
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node="false",
        run_browser=False,
        node_result="skipped",
        browser_result="skipped",
    )
    assert ok is True
    assert "PASSED" in msg

    # Node required and success, browser permitted skip
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node=True,
        run_browser="false",
        node_result="success",
        browser_result="skipped",
    )
    assert ok is True
    assert "PASSED" in msg


def test_aggregate_gate_control_09_both_required_suites_successful():
    """9. Both required suites successful must pass aggregate gate."""
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node="true",
        run_browser=True,
        node_result="success",
        browser_result="success",
    )
    assert ok is True
    assert "PASSED" in msg


def test_aggregate_gate_control_10_coverage_decision_job_failure_or_cancellation():
    """10. Coverage decision job failure or cancellation must fail aggregate gate."""
    for bad_status in ["failure", "cancelled", "timed_out", ""]:
        ok, err = evaluate_gate_compliance(
            decision_result=bad_status,
            run_node=True,
            run_browser=True,
            node_result="success",
            browser_result="success",
        )
        assert ok is False
        assert "Coverage decision job did not succeed" in err


def test_control_15_incomplete_test_inventory(tmp_path):
    """15. An incomplete test inventory must fail validation fail-closed."""
    manifest_file = ROOT / "tests" / "native-ci-manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    # Introduce duplicate entry in native-ci
    fake_manifest = tmp_path / "corrupt_manifest.json"
    corrupt = dict(manifest_data)
    corrupt["native-ci"].append(corrupt["native-ci"][0])
    fake_manifest.write_text(json.dumps(corrupt), encoding="utf-8")

    is_valid, msg, _ = validate_test_manifest(fake_manifest, ROOT)
    assert is_valid is False
    assert "Duplicate assignments" in msg


def test_control_targeted_application_regression_not_skipped():
    """Verify that changes affecting photo editor behavior are not skipped."""
    paths = ["static/js/galleryEditor.js"]
    run_node, run_browser, _ = classify_paths(paths)
    assert run_browser is True, "Gallery editor edit must not be skipped"
    assert run_node is True

    paths = ["static/js/modelPicker.js"]
    run_node, run_browser, _ = classify_paths(paths)
    assert run_browser is True, "modelPicker edit must not be skipped"
    assert run_node is True


def test_control_backend_browser_dependencies_trigger_browser():
    """Verify that browser-facing backend APIs and lifecycle trigger browser validation."""
    for backend_path in [
        "routes/gallery/gallery_routes.py",
        "routes/editor_draft_routes.py",
        "routes/email_routes.py",
        "core/auth.py",
        "core/database.py",
        "core/session_manager.py",
        "src/app_initializer.py",
        "src/task_scheduler.py",
        "app.py",
    ]:
        run_node, run_browser, reason = classify_paths([backend_path])
        assert run_browser is True, f"{backend_path} must trigger browser suite"
        assert run_node is True, f"{backend_path} must trigger node suite"
        assert "browser-facing backend" in reason


def test_control_unknown_backend_dependencies_fail_conservative():
    """Verify that unknown backend paths trigger conservative full execution."""
    paths = ["src/unknown_new_subsystem.py"]
    run_node, run_browser, reason = classify_paths(paths)
    assert run_browser is True, "Unknown backend path must trigger browser suite"
    assert run_node is True, "Unknown backend path must trigger node suite"
    assert "Unknown or unclassified" in reason


def test_control_mixed_changes_produce_union():
    """Verify that mixed changes require the union of all necessary suites."""
    # Docs + native node test -> node=True, browser=False
    run_node, run_browser, _ = classify_paths(["docs/README.md", "tests/notesColors.test.mjs"])
    assert run_node is True
    assert run_browser is False

    # Isolated backend + photo editor -> node=True, browser=True
    run_node, run_browser, _ = classify_paths(["tests/test_shards.py", "static/js/editor/brushes.js"])
    assert run_node is True
    assert run_browser is True

    # Docs + route -> node=True, browser=True
    run_node, run_browser, _ = classify_paths(["docs/README.md", "routes/auth_routes.py"])
    assert run_node is True
    assert run_browser is True
