"""Unit tests for tests/_shards.py - the parallel shard planner.

These pin the partition guarantees directly, without running pytest collection:
every test file lands in exactly one shard, the plan is identical in every
parallel job, and a bad ``--shard`` value is rejected rather than quietly
running a subset. They import only the module under test (a test-support
module, not production code) and touch no filesystem.
"""
from pathlib import Path
import tempfile
import json
import os

import pytest
import yaml

from tests._shards import (
    DEFAULT_ITEM_DURATION_SECONDS,
    DEFAULT_ITEM_WEIGHT,
    DEFAULT_SHARD_COUNT,
    MIN_FILE_DURATION_SECONDS,
    MOBILE_LAYOUT_DURATION_SECONDS,
    SLOW_ITEM_DURATION_SECONDS,
    SLOW_ITEM_WEIGHT,
    ShardSpec,
    ShardSpecError,
    accumulate_file_weights,
    default_durations_path,
    default_fixed_shard_loads,
    item_weight,
    load_duration_manifest,
    parse_shard_spec,
    plan_shards,
    relative_file_key,
    shard_loads,
)


def even_weights(count, weight=1.0):
    """``count`` file keys of equal weight, named so sort order is stable."""
    return {f"tests/test_{index:03d}.py": weight for index in range(count)}


# --- parse_shard_spec --------------------------------------------------------

def test_parse_accepts_a_simple_selector():
    assert parse_shard_spec("2/4") == ShardSpec(index=2, count=4)


def test_parse_tolerates_surrounding_whitespace_from_a_shell_variable():
    assert parse_shard_spec("  3/4\n") == ShardSpec(index=3, count=4)


@pytest.mark.parametrize("value", ["", "abc", "1", "1/", "/4", "1/4/4", "1-4", "1 / 4"])
def test_parse_rejects_malformed_selectors(value):
    with pytest.raises(ShardSpecError):
        parse_shard_spec(value)


@pytest.mark.parametrize("value", ["0/4", "5/4", "-1/4", "1/0"])
def test_parse_rejects_out_of_range_selectors(value):
    with pytest.raises(ShardSpecError):
        parse_shard_spec(value)


def test_parse_error_names_the_offending_value():
    with pytest.raises(ShardSpecError, match="9/4"):
        parse_shard_spec("9/4")


def test_single_shard_selects_everything_and_larger_counts_do_not():
    assert parse_shard_spec("1/1").selects_everything is True
    assert parse_shard_spec("1/2").selects_everything is False


def test_spec_renders_as_the_selector_it_came_from():
    assert str(parse_shard_spec("3/4")) == "3/4"


# --- weights -----------------------------------------------------------------

def test_a_slow_item_outweighs_an_ordinary_one():
    assert item_weight(is_slow=True) == SLOW_ITEM_WEIGHT
    assert item_weight(is_slow=False) == DEFAULT_ITEM_WEIGHT
    assert SLOW_ITEM_WEIGHT > DEFAULT_ITEM_WEIGHT


def test_file_weight_sums_the_items_in_that_file():
    weights = accumulate_file_weights([
        ("tests/test_a.py", False),
        ("tests/test_a.py", False),
        ("tests/test_b.py", True),
    ])
    assert weights == {
        "tests/test_a.py": 2 * DEFAULT_ITEM_WEIGHT,
        "tests/test_b.py": SLOW_ITEM_WEIGHT,
    }


def test_file_weights_of_an_empty_collection_are_empty():
    assert accumulate_file_weights([]) == {}


# --- plan_shards: the partition guarantees -----------------------------------

@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 8])
def test_every_file_lands_in_exactly_one_shard(count):
    weights = even_weights(37)
    plan = plan_shards(weights, count)

    assert len(plan) == count
    placements = [key for bucket in plan for key in bucket]
    assert sorted(placements) == sorted(weights)
    assert len(placements) == len(set(placements))


def test_a_single_shard_holds_the_whole_suite():
    weights = even_weights(10)
    assert plan_shards(weights, 1) == (frozenset(weights),)


def test_the_plan_is_identical_for_the_same_input():
    weights = even_weights(50)
    assert plan_shards(weights, 4) == plan_shards(weights, 4)


def test_the_plan_does_not_depend_on_file_insertion_order():
    keys = list(even_weights(20))
    forward = plan_shards({key: 1.0 for key in keys}, 4)
    reversed_order = plan_shards({key: 1.0 for key in reversed(keys)}, 4)
    assert forward == reversed_order


def test_equal_weights_are_spread_evenly():
    weights = even_weights(40)
    plan = plan_shards(weights, 4)
    assert [len(bucket) for bucket in plan] == [10, 10, 10, 10]


def test_shards_may_be_empty_when_files_are_scarcer_than_shards():
    plan = plan_shards(even_weights(2), 4)
    assert sorted(len(bucket) for bucket in plan) == [0, 0, 1, 1]


def test_an_empty_suite_still_yields_the_requested_number_of_shards():
    assert plan_shards({}, 3) == (frozenset(), frozenset(), frozenset())


@pytest.mark.parametrize("count", [0, -1])
def test_plan_rejects_a_nonsensical_shard_count(count):
    with pytest.raises(ShardSpecError):
        plan_shards(even_weights(4), count)


# --- plan_shards: balance ----------------------------------------------------

def test_a_heavy_file_is_offset_by_giving_its_shard_fewer_others():
    # The real shape of the suite: one file of `slow` tests worth about a
    # quarter of the total, and a long tail of ordinary files.
    weights = {"tests/test_heavy.py": 100.0, **even_weights(300)}
    plan = plan_shards(weights, 4)

    assert shard_loads(weights, plan) == (100.0, 100.0, 100.0, 100.0)
    heavy_shard = next(i for i, b in enumerate(plan) if "tests/test_heavy.py" in b)
    assert len(plan[heavy_shard]) == 1


def test_a_file_heavier_than_an_even_share_sets_the_floor_alone():
    # A shard cannot be lighter than its heaviest file, so the packer stops
    # adding to that shard rather than balancing the others against it.
    weights = {"tests/test_heavy.py": 300.0, **even_weights(300)}
    plan = plan_shards(weights, 4)
    loads = shard_loads(weights, plan)

    assert max(loads) == 300.0
    heavy_shard = next(i for i, b in enumerate(plan) if "tests/test_heavy.py" in b)
    assert plan[heavy_shard] == frozenset({"tests/test_heavy.py"})
    others = [load for i, load in enumerate(loads) if i != heavy_shard]
    assert max(others) - min(others) <= 1.0


def test_the_heaviest_files_are_placed_in_different_shards():
    weights = {f"tests/test_slow_{index}.py": 500.0 for index in range(4)}
    weights.update(even_weights(100))
    plan = plan_shards(weights, 4)

    for index in range(4):
        holders = [bucket for bucket in plan if f"tests/test_slow_{index}.py" in bucket]
        assert len(holders) == 1
    assert all(
        sum(1 for key in bucket if key.startswith("tests/test_slow_")) == 1
        for bucket in plan
    )


def test_shard_loads_account_for_every_file():
    weights = {"tests/test_a.py": 2.0, "tests/test_b.py": 3.0, "tests/test_c.py": 5.0}
    assert sum(shard_loads(weights, plan_shards(weights, 2))) == 10.0


def test_plan_shards_with_fixed_loads_offsets_asymmetric_workloads():
    # 4 shards, shard 0 carries 48.0s fixed load (mobile layout)
    # 400 test files with 1.0s weight each (total 400s)
    # Total work = 400 + 48 = 448s, target load = 112s per shard.
    # Shard 0 should receive 112 - 48 = 64 files.
    # Shards 1, 2, 3 should each receive 112 files.
    weights = even_weights(400, weight=1.0)
    fixed_loads = {0: 48.0}
    plan = plan_shards(weights, 4, fixed_loads=fixed_loads)

    # Exhaustive and disjoint
    all_files = [f for b in plan for f in b]
    assert sorted(all_files) == sorted(weights.keys())
    assert len(all_files) == len(weights)

    # Shard 0 receives fewer files
    assert len(plan[0]) == 64
    assert len(plan[1]) == 112
    assert len(plan[2]) == 112
    assert len(plan[3]) == 112

    # Total combined loads are balanced
    total_loads = shard_loads(weights, plan, fixed_loads=fixed_loads)
    assert total_loads == (112.0, 112.0, 112.0, 112.0)


def test_plan_shards_preserves_exhaustive_disjoint_with_asymmetric_sequence():
    weights = even_weights(55, weight=2.0)
    fixed_loads = [10.0, 0.0, 5.0, 20.0]
    plan = plan_shards(weights, 4, fixed_loads=fixed_loads)

    assert len(plan) == 4
    all_files = [f for b in plan for f in b]
    assert sorted(all_files) == sorted(weights.keys())
    assert len(all_files) == len(set(all_files))


def test_default_fixed_shard_loads_matches_mobile_layout():
    # When durations are active, shard 0 gets MOBILE_LAYOUT_DURATION_SECONDS for any count >= 1
    for count in (1, 2, 4, 6, 8, 12):
        assert default_fixed_shard_loads(count, durations_active=True) == {
            0: MOBILE_LAYOUT_DURATION_SECONDS
        }

    # When count < 1, returns None
    assert default_fixed_shard_loads(0, durations_active=True) is None
    assert default_fixed_shard_loads(-1, durations_active=True) is None

    # When duration manifest is not active, returns None to preserve legacy scheduling exactly
    for count in (1, 2, 4, 6, 8):
        assert default_fixed_shard_loads(count, durations_active=False) is None


def test_fallback_scheduling_matches_original_scheduler_exactly():
    def original_plan(entries, count=4):
        weights = {}
        for file_key, is_slow in entries:
            weights[file_key] = weights.get(file_key, 0.0) + (SLOW_ITEM_WEIGHT if is_slow else DEFAULT_ITEM_WEIGHT)
        buckets = [set() for _ in range(count)]
        loads = [0.0] * count
        ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
        for file_key, weight in ordered:
            target = min(range(count), key=lambda index: (loads[index], index))
            buckets[target].add(file_key)
            loads[target] += weight
        return tuple(frozenset(b) for b in buckets)

    entries = []
    for i in range(120):
        is_slow = (i % 25 == 0)
        items_count = 1 if is_slow else (5 + (i % 7))
        for _ in range(items_count):
            entries.append((f"tests/test_{i:03d}.py", is_slow))

    weights = accumulate_file_weights(entries, durations=None)
    fixed_loads = default_fixed_shard_loads(4, durations_active=False)
    assert fixed_loads is None

    current_plan = plan_shards(weights, 4, fixed_loads=fixed_loads)
    baseline_plan = original_plan(entries, 4)

    assert current_plan == baseline_plan


# --- relative_file_key -------------------------------------------------------

def test_key_is_relative_to_the_repository_root():
    assert relative_file_key("/repo/tests/test_a.py", "/repo") == "tests/test_a.py"


def test_key_falls_back_to_the_full_path_outside_the_root():
    assert relative_file_key("/elsewhere/test_a.py", "/repo") == "/elsewhere/test_a.py"


def test_key_without_a_root_is_the_path_as_given():
    assert relative_file_key("tests/test_a.py") == "tests/test_a.py"


# --- the default the CI matrix is written against ----------------------------

def test_default_shard_count_matches_the_ci_matrix():
    """A matrix that drifts from the default silently stops running a shard."""
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    python_tests = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["python-tests"]
    shards = python_tests["strategy"]["matrix"]["shard"]
    assert shards == [
        f"{index}/{DEFAULT_SHARD_COUNT}" for index in range(1, DEFAULT_SHARD_COUNT + 1)
    ]


# --- duration manifest loading and fallback ----------------------------------

def test_load_duration_manifest_returns_none_when_file_missing(tmp_path):
    missing_file = tmp_path / "nonexistent.json"
    assert load_duration_manifest(path=missing_file) is None


def test_load_duration_manifest_handles_malformed_json(tmp_path):
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{not valid json", encoding="utf-8")
    assert load_duration_manifest(path=corrupt_file) is None


def test_load_duration_manifest_handles_non_dict_json(tmp_path):
    list_file = tmp_path / "list.json"
    list_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_duration_manifest(path=list_file) is None


def test_load_duration_manifest_rejects_unsupported_or_missing_version(tmp_path):
    # Missing version
    p1 = tmp_path / "no_version.json"
    p1.write_text(json.dumps({"durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=p1) is None

    # Unsupported version
    p2 = tmp_path / "version_2.json"
    p2.write_text(json.dumps({"version": 2, "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=p2) is None

    # Non-integer version
    p3 = tmp_path / "str_version.json"
    p3.write_text(json.dumps({"version": "1", "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=p3) is None


def test_load_duration_manifest_rejects_malformed_durations(tmp_path):
    # Negative duration
    p1 = tmp_path / "neg.json"
    p1.write_text(json.dumps({"version": 1, "durations": {"tests/test_a.py": -1.5}}), encoding="utf-8")
    assert load_duration_manifest(path=p1) is None

    # String duration
    p2 = tmp_path / "str.json"
    p2.write_text(json.dumps({"version": 1, "durations": {"tests/test_a.py": "1.5"}}), encoding="utf-8")
    assert load_duration_manifest(path=p2) is None

    # Boolean duration
    p3 = tmp_path / "bool.json"
    p3.write_text(json.dumps({"version": 1, "durations": {"tests/test_a.py": True}}), encoding="utf-8")
    assert load_duration_manifest(path=p3) is None

    # Invalid file key (not under tests/)
    p4 = tmp_path / "invalid_path.json"
    p4.write_text(json.dumps({"version": 1, "durations": {"core/test_a.py": 1.5}}), encoding="utf-8")
    assert load_duration_manifest(path=p4) is None

    # Empty durations
    p5 = tmp_path / "empty.json"
    p5.write_text(json.dumps({"version": 1, "durations": {}}), encoding="utf-8")
    assert load_duration_manifest(path=p5) is None


def test_load_duration_manifest_parses_valid_version_1_manifest(tmp_path):
    manifest_file = tmp_path / "valid.json"
    content = {
        "version": 1,
        "revision": "abc1234",
        "durations": {
            "tests/test_fast.py": 0.05,
            "tests/test_slow.py": 12.34,
        },
    }
    manifest_file.write_text(json.dumps(content), encoding="utf-8")
    result = load_duration_manifest(path=manifest_file)
    assert result == {
        "tests/test_fast.py": 0.05,
        "tests/test_slow.py": 12.34,
    }


# --- accumulate_file_weights with duration manifest --------------------------

def test_accumulate_file_weights_uses_measured_durations_when_profiled():
    durations = {
        "tests/test_a.py": 5.5,
        "tests/test_b.py": 0.25,
    }
    entries = [
        ("tests/test_a.py", False),
        ("tests/test_a.py", False),
        ("tests/test_b.py", True),  # Measured duration overrides slow marker
    ]
    weights = accumulate_file_weights(entries, durations=durations)
    assert weights == {
        "tests/test_a.py": 5.5,
        "tests/test_b.py": 0.25,
    }


def test_accumulate_file_weights_falls_back_for_unprofiled_files_with_duration_manifest():
    durations = {
        "tests/test_known.py": 2.0,
    }
    entries = [
        ("tests/test_known.py", False),
        ("tests/test_new_fast.py", False),
        ("tests/test_new_fast.py", False),
        ("tests/test_new_slow.py", True),
    ]
    weights = accumulate_file_weights(entries, durations=durations)
    assert weights == {
        "tests/test_known.py": 2.0,
        "tests/test_new_fast.py": pytest.approx(2 * DEFAULT_ITEM_DURATION_SECONDS),
        "tests/test_new_slow.py": pytest.approx(SLOW_ITEM_DURATION_SECONDS),
    }


def test_accumulate_file_weights_preserves_legacy_heuristics_when_durations_none():
    entries = [
        ("tests/test_known.py", False),
        ("tests/test_new_fast.py", False),
        ("tests/test_new_fast.py", False),
        ("tests/test_new_slow.py", True),
    ]
    weights = accumulate_file_weights(entries, durations=None)
    assert weights == {
        "tests/test_known.py": DEFAULT_ITEM_WEIGHT,
        "tests/test_new_fast.py": 2 * DEFAULT_ITEM_WEIGHT,
        "tests/test_new_slow.py": SLOW_ITEM_WEIGHT,
    }


def test_unprofiled_fast_tests_do_not_dominate_profiled_files():
    durations = {
        "tests/test_heavy.py": 5.0,
    }
    entries = [("tests/test_heavy.py", False)] + [("tests/test_unprofiled_fast.py", False) for _ in range(50)]
    weights = accumulate_file_weights(entries, durations=durations)
    assert weights["tests/test_heavy.py"] == 5.0
    assert weights["tests/test_unprofiled_fast.py"] == pytest.approx(50 * DEFAULT_ITEM_DURATION_SECONDS)
    assert weights["tests/test_unprofiled_fast.py"] < weights["tests/test_heavy.py"]


def test_partially_populated_duration_manifest_partition_stability():
    durations = {
        "tests/test_a.py": 10.0,
        "tests/test_b.py": 6.0,
    }
    entries = [
        ("tests/test_a.py", False),
        ("tests/test_b.py", False),
        ("tests/test_c_unprofiled.py", False),
        ("tests/test_d_unprofiled_slow.py", True),
    ]
    weights = accumulate_file_weights(entries, durations=durations)
    plan = plan_shards(weights, count=2)
    all_files = {f for shard in plan for f in shard}
    assert all_files == {"tests/test_a.py", "tests/test_b.py", "tests/test_c_unprofiled.py", "tests/test_d_unprofiled_slow.py"}
    assert len(plan[0] & plan[1]) == 0


def test_load_duration_manifest_rejects_nan_and_inf(tmp_path):
    manifest_file = tmp_path / "nan_inf.json"
    manifest_file.write_text(json.dumps({
        "version": 1,
        "durations": {
            "tests/test_valid.py": 1.5,
            "tests/test_nan.py": float("nan"),
        }
    }), encoding="utf-8")
    assert load_duration_manifest(path=manifest_file) is None


def test_accumulate_file_weights_enforces_minimum_positive_floor():
    durations = {
        "tests/test_instant.py": 0.0,
    }
    weights = accumulate_file_weights([("tests/test_instant.py", False)], durations=durations)
    assert weights["tests/test_instant.py"] >= MIN_FILE_DURATION_SECONDS


# --- duration telemetry generation utility tests -----------------------------

def test_junit_xml_parsing_aggregates_file_durations(tmp_path):
    from scripts.generate_shard_durations import parse_junit_xml

    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" time="4.5">
    <testcase classname="tests.test_one" file="tests/test_one.py" name="test_alpha" time="1.200"/>
    <testcase classname="tests.test_one" file="tests/test_one.py" name="test_beta" time="0.800"/>
    <testcase classname="tests.test_two" file="tests/test_two.py" name="test_gamma[1]" time="2.500"/>
  </testsuite>
</testsuites>
"""
    report_file = tmp_path / "junit.xml"
    report_file.write_text(xml_content, encoding="utf-8")

    durations = parse_junit_xml(report_file, repo_root=tmp_path)
    assert durations["tests/test_one.py"] == pytest.approx(2.0, abs=1e-3)
    assert durations["tests/test_two.py"] == pytest.approx(2.5, abs=1e-3)


def test_telemetry_merge_with_existing():
    from scripts.generate_shard_durations import merge_with_existing

    existing = {
        "tests/test_old.py": 1.0,
        "tests/test_updated.py": 2.0,
    }
    updates = {
        "tests/test_updated.py": 3.5,
        "tests/test_new.py": 0.5,
    }
    merged = merge_with_existing(existing, updates)
    assert merged == {
        "tests/test_old.py": 1.0,
        "tests/test_updated.py": 3.5,
        "tests/test_new.py": 0.5,
    }


def test_manifest_roundtrip_writing_and_loading(tmp_path):
    from scripts.generate_shard_durations import write_manifest

    manifest_path = tmp_path / ".shard_durations.json"
    data = {
        "tests/test_z.py": 5.1234,
        "tests/test_a.py": 1.9876,
    }
    write_manifest(data, manifest_path)

    # Manifest file must exist and be valid JSON
    assert manifest_path.is_file()

    # Load with tests._shards loader
    loaded = load_duration_manifest(path=manifest_path)
    assert loaded is not None
    assert loaded["tests/test_a.py"] == 1.988
    assert loaded["tests/test_z.py"] == 5.123

    # Check deterministic key ordering in serialized text
    text = manifest_path.read_text(encoding="utf-8")
    assert text.index("tests/test_a.py") < text.index("tests/test_z.py")


def test_normalize_file_key_resolves_nested_classname(tmp_path):
    from scripts.generate_shard_durations import normalize_file_key

    # Create dummy test directory structure in tmp_path
    tests_dir = tmp_path / "tests" / "sub"
    tests_dir.mkdir(parents=True)
    test_file = tests_dir / "test_nested.py"
    test_file.touch()

    # Simulate classname tests.sub.test_nested.TestSuite.test_method
    raw_from_classname = "tests/sub/test_nested/TestSuite.py"
    resolved = normalize_file_key(raw_from_classname, repo_root=tmp_path)
    assert resolved == "tests/sub/test_nested.py"

    # Non-existent file under tests/ is rejected when repo_root has a tests/ dir
    nonexistent = normalize_file_key("tests/sub/test_ghost.py", repo_root=tmp_path)
    assert nonexistent is None


def test_manifest_overwrite_safeguard(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"

    # Initial manifest with 10 files
    initial_data = {f"tests/test_{i:02d}.py": 1.0 for i in range(10)}
    for f in initial_data:
        (tmp_path / f).touch()
    manifest_path.write_text(json.dumps({"version": 1, "durations": initial_data}), encoding="utf-8")

    # Partial report with only 2 files (<50% of existing)
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest">
    <testcase classname="tests.test_00" file="tests/test_00.py" time="1.5"/>
    <testcase classname="tests.test_01" file="tests/test_01.py" time="2.0"/>
  </testsuite>
</testsuites>
"""
    report_file = tmp_path / "partial.xml"
    report_file.write_text(xml_content, encoding="utf-8")

    # Running without --merge or --force should exit with code 4 to prevent partial wipeout
    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(report_file),
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    exit_code = main()
    assert exit_code == 4

    # Running with --merge should succeed and preserve other files
    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(report_file),
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
        "--merge",
    ])
    exit_code_merge = main()
    assert exit_code_merge == 0
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))["durations"]
    assert len(loaded) == 10
    assert loaded["tests/test_00.py"] == 1.5


def _create_shard_report(
    shard_dir: Path,
    shard_str: str,
    testcases: list[tuple[str, float]],
    commit_sha: str = "abc1234",
    run_id: str = "run_100",
    run_attempt: str = "1",
    pytest_outcome: str = "success",
    collected_files: list[str] | None = None,
    include_collected_files: bool = True,
    selected_files: list[str] | None = None,
    selected_test_counts: dict[str, int] | None = None,
    selected_test_names: dict[str, list[str]] | None = None,
    include_selected_metadata: bool = True,
    xml_testcases: list[dict[str, object]] | None = None,
) -> Path:
    shard_dir.mkdir(parents=True, exist_ok=True)
    xml_lines = ['<?xml version="1.0" encoding="utf-8"?>', '<testsuites>', '  <testsuite name="pytest">']
    if xml_testcases is not None:
        for tc in xml_testcases:
            file_key = str(tc.get("file", "tests/test_a.py"))
            duration = float(tc.get("time", 0.0))
            tc_name = str(tc.get("name", Path(file_key).stem))
            classname = str(tc.get("classname", file_key.replace("/", ".").removesuffix(".py")))
            is_skipped = bool(tc.get("skipped", False))
            if is_skipped:
                xml_lines.append(f'    <testcase classname="{classname}" file="{file_key}" name="{tc_name}" time="{duration:.3f}">')
                xml_lines.append(f'      <skipped message="skipped">{file_key}: test skipped</skipped>')
                xml_lines.append('    </testcase>')
            else:
                xml_lines.append(f'    <testcase classname="{classname}" file="{file_key}" name="{tc_name}" time="{duration:.3f}"/>')
    else:
        tc_file_counts: dict[str, int] = {}
        for file_key, _ in testcases:
            tc_file_counts[file_key] = tc_file_counts.get(file_key, 0) + 1
        tc_file_seen: dict[str, int] = {}
        for file_key, duration in testcases:
            tc_file_seen[file_key] = tc_file_seen.get(file_key, 0) + 1
            if tc_file_counts[file_key] > 1:
                tc_name = f"{Path(file_key).stem}_{tc_file_seen[file_key]}"
            else:
                tc_name = Path(file_key).stem
            xml_lines.append(f'    <testcase classname="{file_key.replace("/", ".").removesuffix(".py")}" file="{file_key}" name="{tc_name}" time="{duration:.3f}"/>')
    xml_lines.extend(['  </testsuite>', '</testsuites>', ''])
    xml_file = shard_dir / "pytest-report.xml"
    xml_file.write_text("\n".join(xml_lines), encoding="utf-8")

    meta: dict[str, object] = {
        "schema_version": 1,
        "shard": shard_str,
        "commit_sha": commit_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "pytest_outcome": pytest_outcome,
    }
    if include_collected_files:
        if collected_files is not None:
            meta["collected_files"] = collected_files
        else:
            if shard_str == "1/1":
                meta["collected_files"] = [tc[0] for tc in testcases]
            else:
                meta["collected_files"] = ["tests/test_a.py", "tests/test_b.py"]

    if include_selected_metadata:
        if selected_files is not None:
            meta["selected_files"] = selected_files
        else:
            meta["selected_files"] = sorted(set(tc[0] for tc in testcases))

        if selected_test_counts is not None:
            meta["selected_test_counts"] = selected_test_counts
        else:
            counts: dict[str, int] = {}
            for tc in testcases:
                counts[tc[0]] = counts.get(tc[0], 0) + 1
            meta["selected_test_counts"] = counts

        if selected_test_names is not None:
            meta["selected_test_names"] = selected_test_names
        else:
            names: dict[str, list[str]] = {}
            if xml_testcases is not None:
                for tc in xml_testcases:
                    fk = str(tc.get("file", "tests/test_a.py"))
                    tn = str(tc.get("name", Path(fk).stem))
                    cn = str(tc.get("classname", ""))
                    fmod = fk.replace("/", ".").removesuffix(".py")
                    if cn and cn.startswith(fmod + "."):
                        cls_part = cn[len(fmod) + 1 :].replace(".", "::")
                        ident = f"{cls_part}::{tn}"
                    else:
                        ident = tn
                    names.setdefault(fk, []).append(ident)
            elif testcases:
                tc_file_counts = {}
                for file_key, _ in testcases:
                    tc_file_counts[file_key] = tc_file_counts.get(file_key, 0) + 1
                tc_file_seen = {}
                for file_key, _ in testcases:
                    tc_file_seen[file_key] = tc_file_seen.get(file_key, 0) + 1
                    if tc_file_counts[file_key] > 1:
                        tn = f"{Path(file_key).stem}_{tc_file_seen[file_key]}"
                    else:
                        tn = Path(file_key).stem
                    names.setdefault(file_key, []).append(tn)
            meta["selected_test_names"] = names

    meta_file = shard_dir / "pytest-meta.json"
    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return xml_file


def test_authoritative_regeneration_requires_sidecars(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    report_file = tmp_path / "report1.xml"
    report_file.write_text("<testsuites/>", encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(report_file),
        "--authoritative",
        "--expected-shards", "1",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_duplicate_shards(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    # Two reports for shard 1/2
    r1 = _create_shard_report(tmp_path / "s1_a", "1/2", [("tests/test_a.py", 1.0)])
    r2 = _create_shard_report(tmp_path / "s1_b", "1/2", [("tests/test_b.py", 2.0)])

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_missing_shards(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    # Shard 1/3 and 2/3 provided, but 3/3 missing
    r1 = _create_shard_report(tmp_path / "s1", "1/3", [("tests/test_a.py", 1.0)])
    r2 = _create_shard_report(tmp_path / "s2", "2/3", [("tests/test_b.py", 2.0)])

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "3",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_failed_shard_executions(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    # Shard 2 failed
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)])
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], pytest_outcome="failure")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_mixed_revisions(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], commit_sha="rev_111")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], commit_sha="rev_222")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 5


def test_authoritative_regeneration_rejects_mixed_run_ids(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], run_id="run_aaa")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], run_id="run_bbb")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_missing_pytest_collected_files(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"

    # Repo has 3 python test files
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()
    (tests_dir / "test_c.py").touch()

    # Reports cover only test_a and test_b
    expected_inventory = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], collected_files=expected_inventory)
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], collected_files=expected_inventory)

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_malformed_xml_timing(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()

    # XML testcase without time attribute
    s1_dir = tmp_path / "s1"
    s1_dir.mkdir()
    xml_file = s1_dir / "pytest-report.xml"
    xml_file.write_text("""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest">
    <testcase classname="tests.test_a" file="tests/test_a.py" name="test_a"/>
  </testsuite>
</testsuites>
""", encoding="utf-8")
    meta = {
        "schema_version": 1,
        "shard": "1/1",
        "commit_sha": "abc1234",
        "run_id": "run_1",
        "run_attempt": "1",
        "pytest_outcome": "success",
        "collected_files": ["tests/test_a.py"],
        "selected_files": ["tests/test_a.py"],
        "selected_test_counts": {"tests/test_a.py": 1},
        "selected_test_names": {"tests/test_a.py": ["test_a"]},
    }
    (s1_dir / "pytest-meta.json").write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(xml_file),
        "--authoritative",
        "--expected-shards", "1",
        "--repo-root", str(tmp_path),
    ])
    # Must fail rather than silently converting to 0.0
    assert main() == 1


def test_authoritative_regeneration_preserves_existing_manifest_on_failure(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"
    initial_content = json.dumps({"version": 1, "durations": {"tests/test_preserved.py": 9.99}})
    manifest_path.write_text(initial_content, encoding="utf-8")

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], commit_sha="rev_111")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], commit_sha="rev_222")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    exit_code = main()
    assert exit_code == 5
    # Manifest content unchanged
    assert manifest_path.read_text(encoding="utf-8") == initial_content


def test_authoritative_regeneration_success(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"

    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.250)], commit_sha="rev_valid", run_id="run_valid")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 3.750)], commit_sha="rev_valid", run_id="run_valid")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--revision", "rev_valid",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 0

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["revision"] == "rev_valid"
    assert data["run_id"] == "run_valid"
    assert data["shard_count"] == 2
    assert data["test_files_count"] == 2
    assert data["durations"] == {
        "tests/test_a.py": 1.250,
        "tests/test_b.py": 3.750,
    }

    # Verify loader parses it cleanly
    loaded = load_duration_manifest(path=manifest_path)
    assert loaded == {
        "tests/test_a.py": 1.250,
        "tests/test_b.py": 3.750,
    }


def test_authoritative_regeneration_rejects_missing_run_attempt(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], run_attempt="")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], run_attempt="1")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_mixed_run_attempts(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], run_attempt="1")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], run_attempt="2")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_overlapping_test_files(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import aggregate_reports, main

    # Two distinct shard reports both containing tests/test_a.py
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 5.0)])
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_a.py", 5.0)])

    # Direct aggregate_reports call with strict=True must raise ValueError
    with pytest.raises(ValueError, match="Overlapping test file detected across shards"):
        aggregate_reports([r1, r2], repo_root=tmp_path, strict=True)

    # CLI in authoritative mode must exit with error
    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() in (1, 2)


def test_aggregate_reports_sums_multiple_testcases_in_same_file_and_shard(tmp_path):
    from scripts.generate_shard_durations import aggregate_reports

    s1_dir = tmp_path / "s1"
    s1_dir.mkdir(parents=True)
    xml_file = s1_dir / "pytest-report.xml"
    xml_file.write_text("""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest">
    <testcase classname="tests.test_a" file="tests/test_a.py" name="test_one" time="2.500"/>
    <testcase classname="tests.test_a" file="tests/test_a.py" name="test_two" time="1.500"/>
  </testsuite>
</testsuites>
""", encoding="utf-8")

    result = aggregate_reports([xml_file], repo_root=tmp_path, strict=True)
    assert result == {"tests/test_a.py": 4.0}


def test_authoritative_regeneration_uses_actual_pytest_collection_inventory(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"

    # Repo has active test files, plus an uncollected empty file and a node test file
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()
    (tests_dir / "test_empty_no_tests.py").touch()
    (tests_dir / "component.test.mjs").touch()

    # Both shard sidecars specify exact pytest collected file inventory
    actual_inventory = ["tests/test_a.py", "tests/test_b.py"]
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2", [("tests/test_a.py", 1.250)],
        commit_sha="rev_valid", run_id="run_valid", run_attempt="1",
        collected_files=actual_inventory,
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2", [("tests/test_b.py", 3.750)],
        commit_sha="rev_valid", run_id="run_valid", run_attempt="1",
        collected_files=actual_inventory,
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--revision", "rev_valid",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    # Must succeed because collection inventory matches reports exactly
    assert main() == 0

    # If sidecar expects a collected file that was not executed, must reject with code 2
    r2_incomplete = _create_shard_report(
        tmp_path / "s2_incomplete", "2/2", [("tests/test_b.py", 3.750)],
        commit_sha="rev_valid", run_id="run_valid", run_attempt="1",
        collected_files=["tests/test_a.py", "tests/test_b.py", "tests/test_unexecuted.py"],
    )
    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2_incomplete),
        "--authoritative",
        "--expected-shards", "2",
        "--revision", "rev_valid",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_empty_inventories_on_all_shards(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"
    initial_content = json.dumps({"version": 1, "durations": {"tests/test_p.py": 1.0}})
    manifest_path.write_text(initial_content, encoding="utf-8")

    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Both shards provide an empty list for collected_files
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], collected_files=[])
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], collected_files=[])

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    # Must reject rather than accepting empty inventory
    assert main() == 2
    assert manifest_path.read_text(encoding="utf-8") == initial_content


def test_authoritative_regeneration_rejects_absent_inventories_on_all_shards(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"
    initial_content = json.dumps({"version": 1, "durations": {"tests/test_p.py": 1.0}})
    manifest_path.write_text(initial_content, encoding="utf-8")

    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Both shards completely omit collected_files from their sidecars
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], include_collected_files=False)
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], include_collected_files=False)

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    # Must reject and must not fall back to filename discovery
    assert main() == 2
    assert manifest_path.read_text(encoding="utf-8") == initial_content


def test_authoritative_regeneration_rejects_inconsistent_inventories_across_shards(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()
    (tests_dir / "test_c.py").touch()

    # Shard 1 collected test_a and test_b; Shard 2 collected test_a and test_c
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)],
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)],
        collected_files=["tests/test_a.py", "tests/test_c.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_malformed_inventory_types(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Case 1: string instead of list
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], collected_files="not-a-list")
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], collected_files=["tests/test_a.py", "tests/test_b.py"])

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2

    # Case 2: non-string items inside list
    r1_nonstr = _create_shard_report(tmp_path / "s1_nonstr", "1/2", [("tests/test_a.py", 1.0)], collected_files=[123, 456])
    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1_nonstr), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_validates_collected_inventory_cli_consistency(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    sidecar_inventory = ["tests/test_a.py", "tests/test_b.py"]
    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)], collected_files=sidecar_inventory)
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)], collected_files=sidecar_inventory)

    # Inconsistent CLI inventory
    cli_inv_file = tmp_path / "cli-inventory.json"
    cli_inv_file.write_text(json.dumps(["tests/test_a.py", "tests/test_c.py"]), encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--collected-inventory", str(cli_inv_file),
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2

    # Matching CLI inventory succeeds
    cli_inv_file.write_text(json.dumps(sidecar_inventory), encoding="utf-8")
    assert main() == 0


def test_authoritative_regeneration_fails_when_any_shard_has_missing_inventory(tmp_path, monkeypatch):
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Shard 1 has valid inventory, Shard 2 omits it
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)],
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)],
        include_collected_files=False,
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_manifest_version_type_validation(tmp_path):
    """P3: Validate that load_duration_manifest strictly checks the version type as int."""
    manifest_path = tmp_path / ".shard_durations.json"

    # Valid integer version 1
    manifest_path.write_text(json.dumps({"version": 1, "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) == {"tests/test_a.py": 1.0}

    # Boolean True (in Python isinstance(True, int) is True, but type(True) is bool)
    manifest_path.write_text(json.dumps({"version": True, "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) is None

    # String "1"
    manifest_path.write_text(json.dumps({"version": "1", "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) is None

    # List [1]
    manifest_path.write_text(json.dumps({"version": [1], "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) is None

    # Dict {"v": 1}
    manifest_path.write_text(json.dumps({"version": {"v": 1}, "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) is None

    # Unsupported integer version 2
    manifest_path.write_text(json.dumps({"version": 2, "durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) is None

    # Missing version
    manifest_path.write_text(json.dumps({"durations": {"tests/test_a.py": 1.0}}), encoding="utf-8")
    assert load_duration_manifest(path=manifest_path) is None


def test_authoritative_regeneration_rejects_incomplete_testcase_execution(tmp_path, monkeypatch):
    """P2: Reject authoritative reports when a shard executes fewer testcases than expected."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Shard 1 sidecar claims tests/test_a.py had 2 tests, but XML report only contains 1
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 2},
        selected_test_names={"tests/test_a.py": ["test_one", "test_two"]},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_unexpected_file_in_shard_report(tmp_path, monkeypatch):
    """P2: Reject shard reports containing testcases for unassigned files."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Shard 1 XML contains testcases for test_b even though not assigned to shard 1
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0), ("tests/test_b.py", 0.5)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_shard_partition_omission(tmp_path, monkeypatch):
    """P2: Reject authoritative reports when shard selected_files omit collected files."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()
    (tests_dir / "test_c.py").touch()

    collected = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    # Shard 1 runs test_a, Shard 2 runs test_b, but test_c was assigned to no shard
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        collected_files=collected,
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=collected,
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_handles_skipped_only_files(tmp_path, monkeypatch):
    """P2: Files with exclusively skipped tests are omitted from manifest and retain heuristic fallback."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"
    (tests_dir / "test_active.py").touch()
    (tests_dir / "test_skipped.py").touch()

    collected = ["tests/test_active.py", "tests/test_skipped.py"]
    # Shard 1 has active tests that ran
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_active.py", 2.500)],
        selected_files=["tests/test_active.py"],
        selected_test_counts={"tests/test_active.py": 1},
        collected_files=collected,
    )
    # Shard 2 has test_skipped where all 2 tests were skipped
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[],
        xml_testcases=[
            {"file": "tests/test_skipped.py", "name": "test_s1", "time": 0.0, "skipped": True},
            {"file": "tests/test_skipped.py", "name": "test_s2", "time": 0.0, "skipped": True},
        ],
        selected_files=["tests/test_skipped.py"],
        selected_test_counts={"tests/test_skipped.py": 2},
        collected_files=collected,
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 0

    # Manifest must contain test_active, but NOT test_skipped (treated as unprofiled)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "tests/test_active.py" in data["durations"]
    assert "tests/test_skipped.py" not in data["durations"]
    assert data["durations"]["tests/test_active.py"] == 2.500

    # Verify that accumulate_file_weights falls back to deterministic heuristic for unprofiled test_skipped
    loaded = load_duration_manifest(path=manifest_path)
    file_items = [
        ("tests/test_active.py", False),
        ("tests/test_skipped.py", False),
        ("tests/test_skipped.py", False),
    ]
    weights = accumulate_file_weights(file_items, durations=loaded)
    # test_active has measured weight
    assert weights["tests/test_active.py"] == 2.500
    # test_skipped has heuristic fallback (2 tests * fallback_per_test 0.018 = 0.036)
    assert weights["tests/test_skipped.py"] == pytest.approx(0.036, abs=1e-3)


def test_real_pytest_junit_total_duration_integration(tmp_path):
    """P2: Verify real pytest JUnit generation with junit_duration_report=total and fixture execution costs."""
    import subprocess
    import sys
    from scripts.generate_shard_durations import parse_junit_xml_details, write_manifest

    test_dir = tmp_path / "tests"
    test_dir.mkdir(parents=True)

    # File with setup/teardown fixture overhead and parametrization
    fixture_test_file = test_dir / "test_with_fixtures.py"
    fixture_test_file.write_text("""
import time
import pytest

@pytest.fixture(autouse=True)
def slow_fixture():
    time.sleep(0.04)
    yield
    time.sleep(0.02)

@pytest.mark.parametrize("param", [1, 2])
def test_parametrized(param):
    time.sleep(0.01)
""", encoding="utf-8")

    # File with only skipped tests
    skipped_test_file = test_dir / "test_all_skipped.py"
    skipped_test_file.write_text("""
import pytest

@pytest.mark.skip(reason="integration test skip")
def test_one():
    pass

@pytest.mark.skip(reason="integration test skip")
def test_two():
    pass
""", encoding="utf-8")

    # Fast test file
    fast_test_file = test_dir / "test_fast.py"
    fast_test_file.write_text("""
def test_fast():
    pass
""", encoding="utf-8")

    junit_report = tmp_path / "pytest-real-report.xml"

    # Run real pytest with -o junit_duration_report=total
    cmd = [
        sys.executable,
        "-m", "pytest",
        str(test_dir),
        "-q",
        f"--junitxml={junit_report}",
        "-o", "junit_duration_report=total",
    ]
    res = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode == 0
    assert junit_report.is_file()

    # Parse real pytest report with parse_junit_xml_details
    details = parse_junit_xml_details(junit_report, repo_root=tmp_path, strict=True)

    # 1. Parameterized test with fixtures produced 2 testcases
    assert details.file_testcase_counts["tests/test_with_fixtures.py"] == 2
    # With total duration report, time for 2 testcases with 0.06s fixture + 0.01s call each is >= 0.10s
    assert details.file_durations["tests/test_with_fixtures.py"] >= 0.10

    # 2. Skipped-only file identified
    assert "tests/test_all_skipped.py" in details.skipped_only_files
    assert details.file_testcase_counts["tests/test_all_skipped.py"] == 2
    assert details.file_skipped_counts["tests/test_all_skipped.py"] == 2

    # 3. Fast test file
    assert details.file_testcase_counts["tests/test_fast.py"] == 1
    assert "tests/test_fast.py" not in details.skipped_only_files

    # 4. Manifest roundtrip: skipped-only omitted
    manifest_path = tmp_path / ".shard_durations.json"
    valid_durations = {
        k: v for k, v in details.file_durations.items()
        if k not in details.skipped_only_files
    }
    write_manifest(valid_durations, manifest_path)
    loaded = load_duration_manifest(path=manifest_path)
    assert "tests/test_with_fixtures.py" in loaded
    assert "tests/test_fast.py" in loaded
    assert "tests/test_all_skipped.py" not in loaded


def test_authoritative_regeneration_rejects_merge(tmp_path, monkeypatch):
    """P2: Reject --merge in authoritative mode so output is always a fresh snapshot."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(tmp_path / "s1", "1/2", [("tests/test_a.py", 1.0)])
    r2 = _create_shard_report(tmp_path / "s2", "2/2", [("tests/test_b.py", 2.0)])

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--merge",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_duplicate_testcase_replacing_another(tmp_path, monkeypatch):
    """P3: Reject authoritative report where a duplicate testcase replaces another test."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # tests/test_a.py expects 2 testcases (count=2), but report contains duplicate 'test_one' twice
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[],
        xml_testcases=[
            {"file": "tests/test_a.py", "name": "test_one", "time": 1.0},
            {"file": "tests/test_a.py", "name": "test_one", "time": 1.0},
        ],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 2},
        selected_test_names={"tests/test_a.py": ["test_one", "test_two"]},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    # Must reject with code 2 due to duplicate testcase detection
    assert main() == 2


def test_authoritative_regeneration_rejects_missing_selected_test_names(tmp_path, monkeypatch):
    """P2: Reject authoritative report missing selected_test_names in sidecar."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    # Remove selected_test_names from s1 sidecar
    s1_meta = tmp_path / "s1" / "pytest-meta.json"
    meta = json.loads(s1_meta.read_text(encoding="utf-8"))
    del meta["selected_test_names"]
    s1_meta.write_text(json.dumps(meta), encoding="utf-8")

    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_invalid_selected_test_names_type(tmp_path, monkeypatch):
    """P2: Reject authoritative report where selected_test_names is not a dict."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    s1_meta = tmp_path / "s1" / "pytest-meta.json"
    meta = json.loads(s1_meta.read_text(encoding="utf-8"))
    meta["selected_test_names"] = ["test_a"]
    s1_meta.write_text(json.dumps(meta), encoding="utf-8")

    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_missing_or_unexpected_file_in_selected_test_names(tmp_path, monkeypatch):
    """P2: Reject authoritative report when keys in selected_test_names do not match selected_files."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # Case 1: Missing file key in selected_test_names
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        selected_test_names={},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2

    # Case 2: Extra file key in selected_test_names
    s1_meta = tmp_path / "s1" / "pytest-meta.json"
    meta = json.loads(s1_meta.read_text(encoding="utf-8"))
    meta["selected_test_names"] = {
        "tests/test_a.py": ["test_a"],
        "tests/test_extra.py": ["test_extra"],
    }
    s1_meta.write_text(json.dumps(meta), encoding="utf-8")
    assert main() == 2


def test_authoritative_regeneration_rejects_empty_or_invalid_testcase_names(tmp_path, monkeypatch):
    """P2: Reject authoritative report with empty lists, non-string, or empty string test names."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    s1_meta = tmp_path / "s1" / "pytest-meta.json"
    meta = json.loads(s1_meta.read_text(encoding="utf-8"))

    # Empty list
    meta["selected_test_names"] = {"tests/test_a.py": []}
    s1_meta.write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2

    # Non-string element
    meta["selected_test_names"] = {"tests/test_a.py": [123]}
    s1_meta.write_text(json.dumps(meta), encoding="utf-8")
    assert main() == 2

    # Empty string element
    meta["selected_test_names"] = {"tests/test_a.py": ["   "]}
    s1_meta.write_text(json.dumps(meta), encoding="utf-8")
    assert main() == 2


def test_authoritative_regeneration_rejects_duplicate_testcase_in_sidecar_names(tmp_path, monkeypatch):
    """P2: Reject authoritative report where selected_test_names contains duplicate test names."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 2},
        selected_test_names={"tests/test_a.py": ["test_same", "test_same"]},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_count_and_names_length_mismatch(tmp_path, monkeypatch):
    """P2: Reject authoritative report where selected_test_counts disagrees with selected_test_names length."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[("tests/test_a.py", 1.0)],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 2},
        selected_test_names={"tests/test_a.py": ["test_single"]},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_rejects_name_discrepancy_between_junit_xml_and_sidecar(tmp_path, monkeypatch):
    """P2: Reject authoritative report when reported JUnit test names differ from expected names even when counts match."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    # XML report has test_reported, sidecar expects test_expected (both count=1)
    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[],
        xml_testcases=[
            {"file": "tests/test_a.py", "name": "test_reported", "time": 1.0},
        ],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        selected_test_names={"tests/test_a.py": ["test_expected"]},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2


def test_authoritative_regeneration_preserves_manifest_on_identity_failure(tmp_path, monkeypatch):
    """P2: Any testcase identity failure must preserve existing manifest without modification."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    manifest_path = tests_dir / ".shard_durations.json"
    initial_content = json.dumps({"version": 1, "durations": {"tests/test_preserved.py": 4.567}})
    manifest_path.write_text(initial_content, encoding="utf-8")

    (tests_dir / "test_a.py").touch()
    (tests_dir / "test_b.py").touch()

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[],
        xml_testcases=[
            {"file": "tests/test_a.py", "name": "test_mismatch", "time": 1.0},
        ],
        selected_files=["tests/test_a.py"],
        selected_test_counts={"tests/test_a.py": 1},
        selected_test_names={"tests/test_a.py": ["test_expected"]},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_b.py", 2.0)],
        selected_files=["tests/test_b.py"],
        selected_test_counts={"tests/test_b.py": 1},
        collected_files=["tests/test_a.py", "tests/test_b.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 2
    assert manifest_path.read_text(encoding="utf-8") == initial_content


def test_collection_export_isolated_from_non_shard_runs(tmp_path, monkeypatch):
    """P2: Verify collection export only runs under sharding and pops environment variable."""
    from tests.conftest import pytest_collection_modifyitems

    export_target = tmp_path / "pytest-collection.json"
    monkeypatch.setenv("PYTEST_COLLECTION_EXPORT", str(export_target))

    class FakeItem:
        def __init__(self, path_str: str, name: str):
            self.path = Path(path_str)
            self.fspath = path_str
            self.name = name
            self.nodeid = f"{path_str}::{name}"
        def get_closest_marker(self, name):
            return None
        def add_marker(self, marker):
            pass

    class FakeConfig:
        rootpath = tmp_path
        def getoption(self, name):
            if name == "shard":
                return None  # No shard specified (e.g. child focus run)
            return None

    items = [FakeItem(str(tmp_path / "tests" / "test_a.py"), "test_one")]

    # When no --shard is passed, export must NOT run
    pytest_collection_modifyitems(FakeConfig(), items)
    assert not export_target.exists()

    # Now simulate shard execution (--shard 1/2)
    class FakeShardConfig:
        rootpath = tmp_path
        def getoption(self, name):
            if name == "shard":
                return "1/2"
            return None
        class hook:
            @staticmethod
            def pytest_deselected(items):
                pass

    monkeypatch.setenv("PYTEST_COLLECTION_EXPORT", str(export_target))
    pytest_collection_modifyitems(FakeShardConfig(), items)

    # Export file must be created
    assert export_target.exists()
    data = json.loads(export_target.read_text(encoding="utf-8"))
    assert "selected_test_names" in data
    assert data["selected_test_names"] == {"tests/test_a.py": ["test_one"]}
    # PYTEST_COLLECTION_EXPORT must be popped from os.environ
    assert "PYTEST_COLLECTION_EXPORT" not in os.environ


def test_authoritative_regeneration_supports_class_based_tests_with_identical_method_names(tmp_path, monkeypatch):
    """P2: Verify class-based tests with identical method names reconcile via class qualification."""
    from scripts.generate_shard_durations import main

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_classes.py").touch()
    (tests_dir / "test_other.py").touch()
    manifest_path = tests_dir / ".shard_durations.json"

    r1 = _create_shard_report(
        tmp_path / "s1", "1/2",
        testcases=[],
        xml_testcases=[
            {"file": "tests/test_classes.py", "classname": "tests.test_classes.TestOne", "name": "test_dup", "time": 1.0},
            {"file": "tests/test_classes.py", "classname": "tests.test_classes.TestTwo", "name": "test_dup", "time": 1.5},
        ],
        selected_files=["tests/test_classes.py"],
        selected_test_counts={"tests/test_classes.py": 2},
        selected_test_names={"tests/test_classes.py": ["TestOne::test_dup", "TestTwo::test_dup"]},
        collected_files=["tests/test_classes.py", "tests/test_other.py"],
    )
    r2 = _create_shard_report(
        tmp_path / "s2", "2/2",
        testcases=[("tests/test_other.py", 2.0)],
        selected_files=["tests/test_other.py"],
        selected_test_counts={"tests/test_other.py": 1},
        collected_files=["tests/test_classes.py", "tests/test_other.py"],
    )

    monkeypatch.setattr("sys.argv", [
        "generate_shard_durations.py",
        "--input", str(r1), str(r2),
        "--authoritative",
        "--expected-shards", "2",
        "--output", str(manifest_path),
        "--repo-root", str(tmp_path),
    ])
    assert main() == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "tests/test_classes.py" in data["durations"]
    assert data["durations"]["tests/test_classes.py"] == 2.5


# --- Candidate configuration validation (4, 6, and 8 shards) ------------------

@pytest.mark.parametrize("candidate_count", [4, 6, 8])
def test_candidate_shard_counts_exhaustive_and_disjoint(candidate_count):
    """Verify exhaustive and disjoint assignment across candidate shard counts."""
    weights = {f"tests/test_{i:04d}.py": (1.0 + (i % 7) * 2.5) for i in range(250)}
    fixed_loads = default_fixed_shard_loads(candidate_count, durations_active=True)
    plan = plan_shards(weights, candidate_count, fixed_loads=fixed_loads)

    assert len(plan) == candidate_count
    assigned_files = [file_key for shard in plan for file_key in shard]
    assert sorted(assigned_files) == sorted(weights.keys())
    assert len(assigned_files) == len(weights)

    seen = set()
    for shard in plan:
        assert seen.isdisjoint(shard)
        seen.update(shard)


@pytest.mark.parametrize("candidate_count", [4, 6, 8])
def test_candidate_shard_counts_deterministic(candidate_count):
    """Verify that shard plan is purely deterministic regardless of input ordering."""
    items = [(f"tests/test_{i:03d}.py", 1.0 + (i % 5)) for i in range(120)]
    weights_forward = dict(items)
    weights_reversed = dict(reversed(items))
    fixed_loads = default_fixed_shard_loads(candidate_count, durations_active=True)

    plan_forward = plan_shards(weights_forward, candidate_count, fixed_loads=fixed_loads)
    plan_reversed = plan_shards(weights_reversed, candidate_count, fixed_loads=fixed_loads)

    assert plan_forward == plan_reversed


@pytest.mark.parametrize("candidate_count", [4, 6, 8])
def test_candidate_shard_counts_mobile_layout_balancing(candidate_count):
    """Verify mobile layout fixed workload balances total runner work."""
    weights = even_weights(400, weight=1.0)
    fixed_loads = default_fixed_shard_loads(candidate_count, durations_active=True)
    assert fixed_loads == {0: MOBILE_LAYOUT_DURATION_SECONDS}

    plan = plan_shards(weights, candidate_count, fixed_loads=fixed_loads)
    loads = shard_loads(weights, plan, fixed_loads=fixed_loads)

    assert len(plan[0]) < len(plan[1])

    max_load = max(loads)
    min_load = min(loads)
    assert max_load - min_load <= 1.0


def test_candidate_shard_counts_mobile_layout_predicate():
    """Verify that startsWith(matrix.shard, '1/') selects exactly shard 1 for candidates."""
    candidate_matrices = {
        4: [f"{i}/4" for i in range(1, 5)],
        6: [f"{i}/6" for i in range(1, 7)],
        8: [f"{i}/8" for i in range(1, 9)],
    }
    for count, matrix in candidate_matrices.items():
        matched = [shard for shard in matrix if shard.startswith("1/")]
        assert matched == [f"1/{count}"], f"Failed to match exactly shard 1 for {count} shards: {matched}"


@pytest.mark.parametrize("candidate_count", [4, 6, 8])
def test_candidate_shard_counts_manifest_compatibility_and_fallback(candidate_count, tmp_path):
    """Verify manifest loading with measured durations and safe fallback for unprofiled files."""
    manifest_data = {
        "version": 1,
        "revision": "abc1234",
        "durations": {
            "tests/test_profiled_slow.py": 25.0,
            "tests/test_profiled_fast.py": 0.5,
        },
    }
    manifest_file = tmp_path / "test_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    durations = load_duration_manifest(path=manifest_file)
    assert durations is not None

    entries = [
        ("tests/test_profiled_slow.py", False),
        ("tests/test_profiled_fast.py", False),
        ("tests/test_unprofiled_ordinary.py", False),
        ("tests/test_unprofiled_slow.py", True),
    ]
    weights = accumulate_file_weights(entries, durations=durations)

    assert weights["tests/test_profiled_slow.py"] == 25.0
    assert weights["tests/test_profiled_fast.py"] == 0.5
    assert weights["tests/test_unprofiled_ordinary.py"] == DEFAULT_ITEM_DURATION_SECONDS
    assert weights["tests/test_unprofiled_slow.py"] == SLOW_ITEM_DURATION_SECONDS

    fixed_loads = default_fixed_shard_loads(candidate_count, durations_active=True)
    plan = plan_shards(weights, candidate_count, fixed_loads=fixed_loads)
    all_files = [f for shard in plan for f in shard]
    assert sorted(all_files) == sorted(weights.keys())


@pytest.mark.parametrize("candidate_count", [4, 6, 8])
def test_candidate_shard_counts_boundary_and_invalid_selectors(candidate_count):
    """Verify valid and invalid shard spec selectors for candidate configurations."""
    for idx in range(1, candidate_count + 1):
        spec = parse_shard_spec(f"{idx}/{candidate_count}")
        assert spec.index == idx
        assert spec.count == candidate_count
        assert str(spec) == f"{idx}/{candidate_count}"

    for bad in [
        f"0/{candidate_count}",
        f"{candidate_count + 1}/{candidate_count}",
        f"-1/{candidate_count}",
        f"{candidate_count}/0",
        f"1/{candidate_count}/1",
    ]:
        with pytest.raises(ShardSpecError):
            parse_shard_spec(bad)


def test_ci_workflow_mobile_layout_predicate_is_generic():
    """Verify that ci.yml uses a generic predicate for mobile layout checks."""
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    steps = yaml.safe_load(text)["jobs"]["python-tests"]["steps"]
    mobile_steps = [step for step in steps if "tests/e2e/ios-layout.config.cjs" in step.get("run", "")]
    assert len(mobile_steps) == 1
    assert mobile_steps[0]["if"] == (
        "steps.docs-check.outputs.docs_only != 'true' && startsWith(matrix.shard, '1/')"
    )
    assert "matrix.shard == '1/4'" not in text, (
        "ci.yml must not hardcode matrix.shard == '1/4'"
    )
