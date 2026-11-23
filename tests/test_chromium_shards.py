"""Executable negative controls and coverage oracle tests for sharded Chromium CI.

Tests Section 7 scenarios:
1. All four shards succeed with complete coverage.
2. One shard fails (test failure).
3. One shard is cancelled.
4. One shard is unexpectedly skipped.
5. One shard never produces a report (missing shard file).
6. One shard produces an empty report (0 test cases).
7. One shard produces malformed JUnit XML.
8. A test identity appears in two shards (duplicate execution).
9. A test identity is missing from the combined inventory.
10. All reported counts match, but the identities are incorrect.
11. A new Playwright test is added (expected inventory mismatch).
12. A Playwright test is removed or renamed (expected inventory mismatch).
13. Chromium execution is legitimately skipped (coverage policy and gate).
14. Coverage-decision output is missing.
15. Native Node execution fails while Chromium succeeds.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
import sys
import xml.etree.ElementTree as ET
import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / ".github" / "scripts" / "verify_chromium_shards.py"
GATE_SCRIPT = ROOT / ".github" / "scripts" / "coverage_decision.py"

# Import verify_chromium_shards
v_spec = importlib.util.spec_from_file_location("verify_chromium_shards", VERIFY_SCRIPT)
v_mod = importlib.util.module_from_spec(v_spec)
sys.modules["verify_chromium_shards"] = v_mod
v_spec.loader.exec_module(v_mod)
discover_playwright_test_identities = v_mod.discover_playwright_test_identities
verify_chromium_shards = v_mod.verify_chromium_shards
ChromiumTestIdentity = v_mod.ChromiumTestIdentity

# Import coverage_decision
g_spec = importlib.util.spec_from_file_location("coverage_decision", GATE_SCRIPT)
g_mod = importlib.util.module_from_spec(g_spec)
g_spec.loader.exec_module(g_mod)
evaluate_gate_compliance = g_mod.evaluate_gate_compliance


def create_mock_shard_xml(
    tmp_path: Path,
    shard_name: str,
    test_cases: list[Any],
    status: str = "pass",
    hostname: str = "",
) -> Path:
    """Helper to create a valid or modified JUnit XML shard report."""
    shard_file = tmp_path / f"{shard_name}.xml"
    ts = ET.Element("testsuites")
    suite = ET.SubElement(
        ts,
        "testsuite",
        name=shard_name,
        hostname=hostname,
        tests=str(len(test_cases)),
        failures="1" if status == "failure" else "0",
        errors="1" if status == "error" else "0",
        skipped="1" if status == "skipped" else "0",
    )
    for item in test_cases:
        if hasattr(item, "project"):
            proj = item.project
            spec = item.file
            title = item.title
            if proj and not suite.get("hostname"):
                suite.set("hostname", proj)
        elif isinstance(item, tuple) and len(item) == 3:
            proj, spec, title = item
            if proj and not suite.get("hostname"):
                suite.set("hostname", str(proj))
        elif isinstance(item, tuple) and len(item) == 2:
            spec, title = item
        else:
            raise ValueError(f"Unknown test case format: {item!r}")

        tc = ET.SubElement(suite, "testcase", classname=spec, name=title, time="1.0")
        if status == "failure":
            f = ET.SubElement(tc, "failure", message="Assertion error")
            f.text = "Traceback details"
        elif status == "error":
            e = ET.SubElement(tc, "error", message="Runtime crash")
            e.text = "Crash stack"
        elif status == "skipped":
            s = ET.SubElement(tc, "skipped")
    tree = ET.ElementTree(ts)
    tree.write(shard_file)
    return shard_file


@pytest.fixture
def mock_inventory_and_shards(tmp_path):
    """Provides a realistic 4-shard test distribution matching expected inventory."""
    expected = discover_playwright_test_identities(ROOT)
    sorted_expected = sorted(expected)
    # Split 137 into 35, 39, 32, 31
    shards_data = [
        sorted_expected[0:35],
        sorted_expected[35:74],
        sorted_expected[74:106],
        sorted_expected[106:137],
    ]
    shard_files = []
    for i, data in enumerate(shards_data, 1):
        f = create_mock_shard_xml(tmp_path, f"shard-{i}", data)
        shard_files.append(f)
    return expected, shard_files, shards_data


def test_scenario_01_all_four_shards_succeed_complete_coverage(mock_inventory_and_shards):
    """1. All four shards succeed with complete coverage."""
    expected, shard_files, _ = mock_inventory_and_shards
    ok, msg, meta = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is True
    assert "Verified all 137 Chromium tests" in msg
    assert meta["executed_tests"] == 137
    assert len(meta["missing_identities"]) == 0
    assert len(meta["duplicate_identities"]) == 0


def test_scenario_02_one_shard_fails(tmp_path, mock_inventory_and_shards):
    """2. One shard fails (test failure)."""
    expected, _, shards_data = mock_inventory_and_shards
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data[0]),
        create_mock_shard_xml(tmp_path, "shard-2", shards_data[1], status="failure"),
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, _ = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "Test failure in" in msg


def test_scenario_03_one_shard_is_cancelled():
    """3. One shard cancelled must fail the aggregate gate."""
    # Matrix job outcome where one shard was cancelled
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node="true",
        run_browser="true",
        node_result="success",
        browser_result=json.dumps({"1": "success", "2": "cancelled", "3": "success", "4": "success"}),
    )
    assert ok is False
    assert "Required Chromium browser suite did not succeed" in msg


def test_scenario_04_one_shard_is_unexpectedly_skipped(tmp_path, mock_inventory_and_shards):
    """4. One shard is unexpectedly skipped."""
    expected, _, shards_data = mock_inventory_and_shards
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data[0]),
        create_mock_shard_xml(tmp_path, "shard-2", shards_data[1], status="skipped"),
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, _ = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "Unexpected skipped test" in msg


def test_scenario_05_one_shard_never_produces_a_report(mock_inventory_and_shards):
    """5. One shard never produces a report (missing shard file)."""
    expected, shard_files, _ = mock_inventory_and_shards
    # Pass only 3 shard files instead of 4
    ok, msg, _ = verify_chromium_shards(shard_files[:3], expected, expected_shard_count=4)
    assert ok is False
    assert "Expected 4 shard reports, found 3" in msg

    # Pass non-existent file
    missing_file = Path("/nonexistent/shard-4.xml")
    ok, msg, _ = verify_chromium_shards(shard_files[:3] + [missing_file], expected, expected_shard_count=4)
    assert ok is False
    assert "does not exist" in msg


def test_scenario_06_one_shard_produces_empty_report(tmp_path, mock_inventory_and_shards):
    """6. One shard produces an empty report (0 test cases)."""
    expected, _, shards_data = mock_inventory_and_shards
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data[0]),
        create_mock_shard_xml(tmp_path, "shard-2", []),  # empty
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, _ = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "zero test cases" in msg


def test_scenario_07_one_shard_produces_malformed_xml(tmp_path, mock_inventory_and_shards):
    """7. One shard produces malformed JUnit XML."""
    expected, _, shards_data = mock_inventory_and_shards
    bad_file = tmp_path / "shard-2-bad.xml"
    bad_file.write_text("<testsuites><testsuite>unclosed tag", encoding="utf-8")
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data[0]),
        bad_file,
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, _ = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "Malformed JUnit XML" in msg


def test_scenario_08_test_identity_appears_in_two_shards(tmp_path, mock_inventory_and_shards):
    """8. A test identity appears in two shards (duplicate execution)."""
    expected, _, shards_data = mock_inventory_and_shards
    # Duplicate case from shard 1 into shard 2
    shards_data_2 = list(shards_data[1]) + [shards_data[0][0]]
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data[0]),
        create_mock_shard_xml(tmp_path, "shard-2", shards_data_2),
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, meta = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "duplicate test execution" in msg
    assert len(meta["duplicate_identities"]) == 1


def test_scenario_09_test_identity_missing_from_combined_inventory(tmp_path, mock_inventory_and_shards):
    """9. A test identity is missing from the combined inventory."""
    expected, _, shards_data = mock_inventory_and_shards
    # Drop one case from shard 1
    shards_data_1 = shards_data[0][1:]
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data_1),
        create_mock_shard_xml(tmp_path, "shard-2", shards_data[1]),
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, meta = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "Missing 1 required test(s)" in msg
    assert len(meta["missing_identities"]) == 1


def test_scenario_10_matching_counts_but_incorrect_identities(tmp_path, mock_inventory_and_shards):
    """10. All reported counts match, but the identities are incorrect."""
    expected, _, shards_data = mock_inventory_and_shards
    # Replace one valid test with a fake test so count remains 35
    fake_case = ("adjustment-layers.spec.js", "Nonexistent synthetic test title")
    shards_data_1 = shards_data[0][:-1] + [fake_case]
    shard_files = [
        create_mock_shard_xml(tmp_path, "shard-1", shards_data_1),
        create_mock_shard_xml(tmp_path, "shard-2", shards_data[1]),
        create_mock_shard_xml(tmp_path, "shard-3", shards_data[2]),
        create_mock_shard_xml(tmp_path, "shard-4", shards_data[3]),
    ]
    ok, msg, meta = verify_chromium_shards(shard_files, expected, expected_shard_count=4)
    assert ok is False
    assert "Missing 1 required test(s)" in msg
    assert len(meta["missing_identities"]) == 1


def test_scenario_11_new_playwright_test_added(mock_inventory_and_shards):
    """11. A new Playwright test is added (expected inventory mismatch)."""
    expected, shard_files, _ = mock_inventory_and_shards
    expanded_expected = expected | {("new-feature.spec.js", "newly added test")}
    ok, msg, meta = verify_chromium_shards(shard_files, expanded_expected, expected_shard_count=4)
    assert ok is False
    assert "Missing 1 required test(s)" in msg
    assert "new-feature.spec.js::newly added test" in meta["missing_identities"]


def test_scenario_12_playwright_test_removed_or_renamed(mock_inventory_and_shards):
    """12. A Playwright test is removed or renamed (unexpected test in shards)."""
    expected, shard_files, _ = mock_inventory_and_shards
    # Expected has fewer tests than what shards executed
    reduced_expected = set(list(expected)[1:])
    ok, msg, meta = verify_chromium_shards(shard_files, reduced_expected, expected_shard_count=4)
    assert ok is False
    assert "unexpected test(s)" in msg
    assert len(meta["unexpected_identities"]) == 1


def test_scenario_13_chromium_execution_legitimately_skipped():
    """13. Chromium execution is legitimately skipped (coverage policy and gate)."""
    # Both skipped via scalar string
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node="false",
        run_browser="false",
        node_result="skipped",
        browser_result="skipped",
    )
    assert ok is True
    assert "PASSED" in msg

    # Matrix json output with all skipped
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node="false",
        run_browser="false",
        node_result="skipped",
        browser_result=json.dumps({"1": "skipped", "2": "skipped", "3": "skipped", "4": "skipped"}),
    )
    assert ok is True
    assert "PASSED" in msg


def test_scenario_14_coverage_decision_output_missing():
    """14. Coverage-decision output is missing."""
    for empty_val in ["", None]:
        ok, msg = evaluate_gate_compliance(
            decision_result="success",
            run_node="true",
            run_browser=empty_val,
            node_result="success",
            browser_result="success",
        )
        assert ok is False
        assert "Invalid or missing Chromium browser decision" in msg


def test_scenario_15_native_node_fails_while_chromium_succeeds():
    """15. Native Node execution fails while Chromium succeeds."""
    ok, msg = evaluate_gate_compliance(
        decision_result="success",
        run_node="true",
        run_browser="true",
        node_result="failure",
        browser_result=json.dumps({"1": "success", "2": "success", "3": "success", "4": "success"}),
    )
    assert ok is False
    assert "Required Native Node suite did not succeed" in msg


def test_scenario_16_directory_discovery_ignores_nested_fixture_xml(tmp_path, mock_inventory_and_shards):
    """16. Main CLI discovers shard reports in subdirectories without including nested fixture xml files."""
    expected, _, shards_data = mock_inventory_and_shards
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    for i, data in enumerate(shards_data, 1):
        shard_dir = artifacts_dir / f"chromium-results-shard-{i}"
        shard_dir.mkdir()
        # Standard report name
        create_mock_shard_xml(shard_dir, "browser-junit", data)

    # Add an extraneous nested test fixture xml in a subfolder of shard 1
    nested_dir = artifacts_dir / "chromium-results-shard-1" / "photo-editor" / "subtest"
    nested_dir.mkdir(parents=True)
    extraneous_xml = nested_dir / "native-email-junit.xml"
    extraneous_xml.write_text("<testsuites><testsuite name='fixture'/></testsuites>")

    # Test main CLI via argv
    main_func = v_mod.main
    code = main_func([str(artifacts_dir), "--expected-shards", "4"])
    assert code == 0


def test_scenario_17_identical_leaf_titles_nested_describes_distinguished(tmp_path):
    """17. Two distinct tests with identical leaf titles under different describes are preserved."""
    test_a = ChromiumTestIdentity("", "editor.spec.js", "Layers › should persist")
    test_b = ChromiumTestIdentity("", "editor.spec.js", "Filters › should persist")
    dummy_1 = ChromiumTestIdentity("", "dummy1.spec.js", "test1")
    dummy_2 = ChromiumTestIdentity("", "dummy2.spec.js", "test2")
    expected = {test_a, test_b, dummy_1, dummy_2}

    s1 = create_mock_shard_xml(tmp_path, "shard-1", [test_a])
    s2 = create_mock_shard_xml(tmp_path, "shard-2", [test_b])
    s3 = create_mock_shard_xml(tmp_path, "shard-3", [dummy_1])
    s4 = create_mock_shard_xml(tmp_path, "shard-4", [dummy_2])

    ok, msg, meta = verify_chromium_shards([s1, s2, s3, s4], expected, expected_shard_count=4)
    assert ok is True
    assert meta["executed_tests"] == 4
    assert len(meta["missing_identities"]) == 0
    assert len(meta["duplicate_identities"]) == 0


def test_scenario_18_identical_leaf_titles_collision_detected_if_one_omitted(tmp_path):
    """18. If a test with an identical leaf title is executed twice and the other omitted, gate rejects."""
    test_a = ChromiumTestIdentity("", "editor.spec.js", "Layers › should persist")
    test_b = ChromiumTestIdentity("", "editor.spec.js", "Filters › should persist")
    dummy_1 = ChromiumTestIdentity("", "dummy1.spec.js", "test1")
    dummy_2 = ChromiumTestIdentity("", "dummy2.spec.js", "test2")
    expected = {test_a, test_b, dummy_1, dummy_2}

    # Shard 2 mistakenly executes test_a again instead of test_b
    s1 = create_mock_shard_xml(tmp_path, "shard-1", [test_a])
    s2 = create_mock_shard_xml(tmp_path, "shard-2", [test_a])
    s3 = create_mock_shard_xml(tmp_path, "shard-3", [dummy_1])
    s4 = create_mock_shard_xml(tmp_path, "shard-4", [dummy_2])

    ok, msg, meta = verify_chromium_shards([s1, s2, s3, s4], expected, expected_shard_count=4)
    assert ok is False
    assert "Found 1 duplicate test execution(s)" in msg
    assert any("Layers › should persist" in d for d in meta["duplicate_identities"])


def test_scenario_19_identical_spec_basenames_in_different_directories(tmp_path):
    """19. Tests with identical filenames in different directories are recognized as distinct."""
    test_mobile = ChromiumTestIdentity("", "mobile/export.spec.js", "PNG export")
    test_desktop = ChromiumTestIdentity("", "desktop/export.spec.js", "PNG export")
    dummy_1 = ChromiumTestIdentity("", "dummy1.spec.js", "test1")
    dummy_2 = ChromiumTestIdentity("", "dummy2.spec.js", "test2")
    expected = {test_mobile, test_desktop, dummy_1, dummy_2}

    s1 = create_mock_shard_xml(tmp_path, "shard-1", [test_mobile])
    s2 = create_mock_shard_xml(tmp_path, "shard-2", [test_desktop])
    s3 = create_mock_shard_xml(tmp_path, "shard-3", [dummy_1])
    s4 = create_mock_shard_xml(tmp_path, "shard-4", [dummy_2])

    ok, msg, meta = verify_chromium_shards([s1, s2, s3, s4], expected, expected_shard_count=4)
    assert ok is True
    assert meta["executed_tests"] == 4

def test_scenario_20_identical_tests_across_projects_distinguished(tmp_path):
    """20. Tests with identical filenames and titles across different projects are distinct."""
    test_desk = ChromiumTestIdentity("desktop-chromium", "auth.spec.js", "login")
    test_mob = ChromiumTestIdentity("mobile-chromium", "auth.spec.js", "login")
    dummy_1 = ChromiumTestIdentity("", "dummy1.spec.js", "test1")
    dummy_2 = ChromiumTestIdentity("", "dummy2.spec.js", "test2")
    expected = {test_desk, test_mob, dummy_1, dummy_2}

    s1 = create_mock_shard_xml(tmp_path, "shard-1", [test_desk], hostname="desktop-chromium")
    s2 = create_mock_shard_xml(tmp_path, "shard-2", [test_mob], hostname="mobile-chromium")
    s3 = create_mock_shard_xml(tmp_path, "shard-3", [dummy_1])
    s4 = create_mock_shard_xml(tmp_path, "shard-4", [dummy_2])

    ok, msg, meta = verify_chromium_shards([s1, s2, s3, s4], expected, expected_shard_count=4)
    assert ok is True
    assert meta["executed_tests"] == 4
