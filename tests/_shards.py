"""Deterministic, balanced partition of the test suite into parallel shards.

The suite runs ~7.8k tests in a single pytest workload. This module splits that
workload into N sections that CI runs as parallel jobs, so wall-clock time is
bounded by the slowest section rather than by the whole suite.

Two properties matter more than speed, and both are structural here rather than
checked after the fact:

* **Exhaustive and disjoint.** Shard assignment is a total function of the test
  *file*, so every test file lands in exactly one shard. No test can be dropped
  by a marker typo, and none runs twice. This is deliberately not built on the
  ``area_*`` taxonomy markers: those are not a partition in practice, because a
  test may also carry a hand-applied ``area_*`` mark on top of the one
  ``tests/conftest.py`` derives from its filename (``test_hwfit_container_
  visibility_warning.py`` carries three).
* **Whole files stay together.** Tests in one file share module state and are
  written to run in file order, so a file is the smallest unit a shard can hold.

Balance uses the existing ``slow`` marker as its weight signal rather than a
committed duration table that would go stale silently. Packing is greedy
longest-processing-time-first, which is deterministic for a given file set -
every parallel job computes the identical plan from the same commit.

This module imports nothing from the application or from pytest - only the
standard library - so the planner is directly unit-testable. The pytest wiring
lives in ``tests/conftest.py``. See ``tests/README.md``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# Number of sections CI runs in parallel. Kept here as documentation of the
# intended default; the shard count actually used comes from the --shard value.
DEFAULT_SHARD_COUNT = 4

# Relative cost of one test item. Measured on dev at 2026-10-02 over a full
# `pytest -q --durations=25` run: the five `slow`-marked items average 9.0 s
# each and the remaining 7789 items average 0.018 s, a ratio of roughly 500.
# Exact values do not matter - only that a `slow` item outweighs a whole
# ordinary file, so the packer spreads the slow ones across sections first.
DEFAULT_ITEM_WEIGHT = 1.0
SLOW_ITEM_WEIGHT = 500.0

_SHARD_SPEC_PATTERN = re.compile(r"\A(\d+)/(\d+)\Z")


class ShardSpecError(ValueError):
    """Raised when a ``--shard`` value is not a usable ``N/M`` selector."""


@dataclass(frozen=True)
class ShardSpec:
    """A one-based shard selector: shard ``index`` of ``count``."""

    index: int
    count: int

    @property
    def selects_everything(self) -> bool:
        """True when the selector is a no-op (``1/1``) and nothing is deselected."""
        return self.count == 1

    def __str__(self) -> str:
        return f"{self.index}/{self.count}"


def parse_shard_spec(value: str) -> ShardSpec:
    """Parse ``"N/M"`` into a :class:`ShardSpec`.

    Rejects anything that would silently run the wrong subset: a malformed
    value, a zero or negative part, or an index past the shard count. Surrounding
    whitespace is tolerated because CI passes the value through a shell variable.
    """
    match = _SHARD_SPEC_PATTERN.match(value.strip())
    if match is None:
        raise ShardSpecError(
            f"invalid shard {value!r}: expected N/M, e.g. 1/{DEFAULT_SHARD_COUNT}"
        )
    index, count = int(match.group(1)), int(match.group(2))
    if count < 1:
        raise ShardSpecError(f"invalid shard {value!r}: shard count must be >= 1")
    if not 1 <= index <= count:
        raise ShardSpecError(
            f"invalid shard {value!r}: shard index must be between 1 and {count}"
        )
    return ShardSpec(index=index, count=count)


def item_weight(is_slow: bool) -> float:
    """Weight of a single test item, by whether it carries the ``slow`` marker."""
    return SLOW_ITEM_WEIGHT if is_slow else DEFAULT_ITEM_WEIGHT


def accumulate_file_weights(entries: Iterable[tuple[str, bool]]) -> dict[str, float]:
    """Sum per-item weights into a per-file total.

    ``entries`` yields ``(file_key, is_slow)`` for each collected test item, so a
    file's weight reflects both how many tests it holds and how slow they are.
    """
    weights: dict[str, float] = {}
    for file_key, is_slow in entries:
        weights[file_key] = weights.get(file_key, 0.0) + item_weight(is_slow)
    return weights


def plan_shards(
    file_weights: Mapping[str, float], count: int
) -> tuple[frozenset[str], ...]:
    """Partition the files of ``file_weights`` into ``count`` balanced shards.

    Greedy longest-processing-time-first: heaviest file first, each one placed in
    the lightest shard so far. Ties break on the file key and then on the lowest
    shard index, so the plan depends only on the input and is identical in every
    parallel job. Returns one frozenset per shard, in shard order; shards may be
    empty when there are fewer files than shards.
    """
    if count < 1:
        raise ShardSpecError(f"shard count must be >= 1, got {count}")
    buckets: list[set[str]] = [set() for _ in range(count)]
    loads = [0.0] * count
    # Heaviest first, with the file key as a deterministic tie-break.
    ordered = sorted(file_weights.items(), key=lambda item: (-item[1], item[0]))
    for file_key, weight in ordered:
        target = min(range(count), key=lambda index: (loads[index], index))
        buckets[target].add(file_key)
        loads[target] += weight
    return tuple(frozenset(bucket) for bucket in buckets)


def shard_loads(
    file_weights: Mapping[str, float], plan: tuple[frozenset[str], ...]
) -> tuple[float, ...]:
    """Total weight of each shard in ``plan`` - the balance the packer achieved."""
    return tuple(
        sum(file_weights[file_key] for file_key in bucket) for bucket in plan
    )


def relative_file_key(path: str | Path, root: str | Path | None = None) -> str:
    """Stable per-file key: the posix path relative to ``root`` when possible.

    Falls back to the absolute posix path when ``path`` lies outside ``root`` or
    the relationship cannot be resolved, which keeps the key defined for every
    collected item rather than dropping one from the plan.
    """
    resolved = Path(path)
    if root is not None:
        try:
            return resolved.resolve().relative_to(Path(root).resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return resolved.as_posix()
