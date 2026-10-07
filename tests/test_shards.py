"""Unit tests for tests/_shards.py - the parallel shard planner.

These pin the partition guarantees directly, without running pytest collection:
every test file lands in exactly one shard, the plan is identical in every
parallel job, and a bad ``--shard`` value is rejected rather than quietly
running a subset. They import only the module under test (a test-support
module, not production code) and touch no filesystem.
"""
from pathlib import Path

import pytest

from tests._shards import (
    DEFAULT_ITEM_WEIGHT,
    DEFAULT_SHARD_COUNT,
    SLOW_ITEM_WEIGHT,
    ShardSpec,
    ShardSpecError,
    accumulate_file_weights,
    item_weight,
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

@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 8])
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
    text = workflow.read_text(encoding="utf-8")
    for index in range(1, DEFAULT_SHARD_COUNT + 1):
        assert f'"{index}/{DEFAULT_SHARD_COUNT}"' in text, (
            f"ci.yml does not run shard {index}/{DEFAULT_SHARD_COUNT}"
        )
    assert f'"{DEFAULT_SHARD_COUNT + 1}/' not in text
