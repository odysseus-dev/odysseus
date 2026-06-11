"""Unit tests for tests/_taxonomy.py - the test-taxonomy classification module.

These tests pin the conservative classification behavior directly, without
running pytest collection. They import only the module under test (a test-support
module, not production code) and touch no filesystem.
"""
import re

import pytest

from tests._taxonomy import (
    classify_test_path,
    discover_markers,
    markers_for_path,
    normalize_marker_name,
)


# --- normalize_marker_name ---------------------------------------------------

def test_normalize_lowercases():
    assert normalize_marker_name("Area_Security") == "area_security"


def test_normalize_converts_nonalphanumeric_runs_to_underscore():
    assert normalize_marker_name("owner--scope..test") == "owner_scope_test"


def test_normalize_strips_leading_and_trailing_underscores():
    assert normalize_marker_name("__owner-scope__") == "owner_scope"


# --- classify_test_path: one example per area --------------------------------

@pytest.mark.parametrize("filename, expected_area, expected_sub", [
    ("test_owner_scope.py", "security", "owner_scope"),
    ("test_cookbook_helpers.py", "services", "cookbook"),
    ("test_routes_sessions.py", "routes", "routes"),
    ("test_backup_cli.py", "cli", "cli"),
    ("test_compare_js.py", "js", "js"),
    ("segmenter.test.mjs", "js", "js"),
    ("segmenter.test.js", "js", "js"),
    ("segmenter.test.ts", "js", "js"),
    ("test_helpers_import_state.py", "helpers", "helpers"),
    ("test_atomic_io.py", "unit", "atomic"),
])
def test_classify_examples(filename, expected_area, expected_sub):
    result = classify_test_path(filename)
    assert result.area == expected_area
    assert result.sub_area == expected_sub


# --- classify_test_path: fallback --------------------------------------------

def test_unknown_filename_is_uncategorized():
    result = classify_test_path("test_widget_gizmo_thing.py")
    assert result.area == "uncategorized"


def test_uncategorized_sub_area_is_derived_from_filename_tokens():
    result = classify_test_path("test_archived_sessions_model_filter.py")
    assert result.area == "uncategorized"
    assert result.sub_area == "archived_sessions_model_filter"


# --- markers_for_path --------------------------------------------------------

def test_markers_for_path_returns_one_area_and_one_sub():
    markers = markers_for_path("test_owner_scope.py")
    assert markers == ("area_security", "sub_owner_scope")
    assert len([m for m in markers if m.startswith("area_")]) == 1
    assert len([m for m in markers if m.startswith("sub_")]) == 1


def test_markers_for_path_are_normalized():
    markers = markers_for_path("test_foo-bar.py")
    assert markers == ("area_uncategorized", "sub_foo_bar")
    for marker in markers:
        assert re.fullmatch(r"[a-z0-9_]+", marker)


# --- discover_markers --------------------------------------------------------

def test_discover_markers_is_sorted_and_deduplicated():
    paths = [
        "test_owner_scope.py",
        "test_owner_scope.py",
        "test_cookbook_helpers.py",
    ]
    markers = discover_markers(paths)
    assert markers == tuple(sorted(set(markers)))
    assert markers == (
        "area_security",
        "area_services",
        "sub_cookbook",
        "sub_owner_scope",
    )


def test_discover_markers_includes_area_and_sub():
    markers = discover_markers(["test_owner_scope.py"])
    assert any(m.startswith("area_") for m in markers)
    assert any(m.startswith("sub_") for m in markers)


# --- edge cases --------------------------------------------------------------

def test_normalize_all_symbols_becomes_empty():
    assert normalize_marker_name("@@@") == ""


def test_bare_test_filename_is_fully_uncategorized():
    result = classify_test_path("tests/test.py")
    assert result.area == "uncategorized"
    assert result.sub_area == "uncategorized"


def test_markers_for_bare_test_filename():
    markers = markers_for_path("tests/test.py")
    assert "area_uncategorized" in markers
    assert "sub_uncategorized" in markers


@pytest.mark.parametrize("path", [
    "tests/helpers/test_module_isolation.py",
    "/work/repo/tests/helpers/test_module_isolation.py",
])
def test_file_under_helpers_dir_is_helpers(path):
    result = classify_test_path(path)
    assert result.area == "helpers"
    assert result.sub_area == "helpers"


# --- priority contract -------------------------------------------------------

def test_security_beats_services_when_both_tokens_present():
    result = classify_test_path("test_email_owner_scope.py")
    assert result.area == "security"
    assert result.sub_area == "owner_scope"


def test_unrelated_helpers_ancestor_is_not_helpers():
    result = classify_test_path("/work/helpers/odysseus/tests/test_owner_scope.py")
    assert result.area == "security"
    assert result.sub_area == "owner_scope"
