"""Deterministic, balanced partition of the test suite into parallel shards.

This module splits a pytest workload into N sections that CI runs as
parallel jobs, with test execution time bounded by the slowest section.

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

Balance uses measured per-file execution durations when available in a committed
manifest, falling back deterministically to item counts and the ``slow`` marker
for unprofiled or newly added test files. Packing is greedy longest-processing-
time-first, which is deterministic for a given file set: every parallel job
computes the identical plan from the same commit.

This module imports nothing from the application or from pytest: only the
standard library: so the planner is directly unit-testable. The pytest wiring
lives in ``tests/conftest.py``. See ``tests/README.md``.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Number of sections in the CI matrix and its authoritative telemetry bundle.
# pytest takes the actual shard count from the --shard value.
DEFAULT_SHARD_COUNT = 6

# Relative cost of one test item. Measured on dev at 2026-10-02 over a full
# `pytest -q --durations=25` run: the five `slow`-marked items average 9.0 s
# each and the remaining 7789 items average 0.018 s, a ratio of roughly 500.
# Exact values do not matter: only that a `slow` item outweighs a whole
# ordinary file, so the packer spreads the slow ones across sections first.
DEFAULT_ITEM_WEIGHT = 1.0
SLOW_ITEM_WEIGHT = 500.0

# Estimated execution durations in seconds for unprofiled items when a
# duration profile is active. Derived directly from the same baseline:
# ordinary items average 0.018 s and slow-marked items average 9.0 s.
# Using seconds as the common scheduling unit prevents fast-test counts from
# distorting duration-aware scheduling against measured file durations.
DEFAULT_ITEM_DURATION_SECONDS = 0.018
SLOW_ITEM_DURATION_SECONDS = 9.0
MIN_FILE_DURATION_SECONDS = 0.001

# Fixed workload duration in seconds for shards running non-pytest CI tasks.
# Shard 1 runs Playwright mobile layout checks (tests/e2e/ios-layout.config.cjs).
MOBILE_LAYOUT_DURATION_SECONDS = 48.0

# Standard name for the committed duration manifest.
DEFAULT_DURATIONS_FILENAME = ".shard_durations.json"

# Supported manifest schema versions.
SUPPORTED_MANIFEST_VERSIONS = {1}

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


def default_durations_path(root: str | Path | None = None) -> Path:
    """Return the default path to the committed shard durations manifest."""
    if root is not None:
        return Path(root) / "tests" / DEFAULT_DURATIONS_FILENAME
    return Path(__file__).resolve().parent / DEFAULT_DURATIONS_FILENAME


def load_duration_manifest(
    path: str | Path | None = None,
    root: str | Path | None = None,
) -> dict[str, float] | None:
    """Load measured per-file test execution durations from a manifest.

    Validates schema version, expected structure, posix file keys, and
    finite positive durations. Returns None on any validation failure,
    falling back deterministically to item-based heuristic weights.
    """
    manifest_path = Path(path) if path is not None else default_durations_path(root)
    if not manifest_path.is_file():
        return None
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    version = data.get("version")
    if type(version) is not int or version not in SUPPORTED_MANIFEST_VERSIONS:
        return None

    raw_durations = data.get("durations")
    if not isinstance(raw_durations, dict) or not raw_durations:
        return None

    durations: dict[str, float] = {}
    for key, value in raw_durations.items():
        if (
            not isinstance(key, str)
            or not key.startswith("tests/")
            or not key.endswith(".py")
            or ".." in key
            or "\\" in key
        ):
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        durations[key] = float(value)

    return durations if durations else None


def accumulate_file_weights(
    entries: Iterable[tuple[str, bool]],
    durations: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Sum per-file weights into a mapping of file_key -> weight.

    ``entries`` yields ``(file_key, is_slow)`` for each collected test item.

    When ``durations`` is provided (a mapping of file_key -> measured duration
    in seconds), the scheduling unit is estimated execution seconds:
    * Profiled files (present in ``durations``) use their measured execution
      duration, bounded below by ``MIN_FILE_DURATION_SECONDS``.
    * Unprofiled files (missing from ``durations``) derive estimated durations
      from test items, where each ordinary item contributes
      ``DEFAULT_ITEM_DURATION_SECONDS`` (0.018 s) and each ``slow``-marked item
      contributes ``SLOW_ITEM_DURATION_SECONDS`` (9.0 s).

    When ``durations`` is None, the original heuristic weighting is preserved:
    each ordinary item contributes ``DEFAULT_ITEM_WEIGHT`` (1.0) and each
    ``slow``-marked item contributes ``SLOW_ITEM_WEIGHT`` (500.0).
    """
    if durations is None:
        weights: dict[str, float] = {}
        for file_key, is_slow in entries:
            weights[file_key] = weights.get(file_key, 0.0) + item_weight(is_slow)
        return weights

    weights: dict[str, float] = {}
    unprofiled_weights: dict[str, float] = {}

    for file_key, is_slow in entries:
        if file_key in durations:
            weights[file_key] = max(durations[file_key], MIN_FILE_DURATION_SECONDS)
        else:
            item_duration = (
                SLOW_ITEM_DURATION_SECONDS if is_slow else DEFAULT_ITEM_DURATION_SECONDS
            )
            unprofiled_weights[file_key] = (
                unprofiled_weights.get(file_key, 0.0) + item_duration
            )

    for file_key, est_duration in unprofiled_weights.items():
        weights[file_key] = max(est_duration, MIN_FILE_DURATION_SECONDS)

    return weights


def default_fixed_shard_loads(
    count: int, durations_active: bool = True
) -> dict[int, float] | None:
    """Return default fixed non-pytest workloads per shard (0-based index).

    In the CI workflow, shard 1 (index 0) executes mobile layout
    validation (~48s) after pytest. When duration profiles are active,
    accounting for this fixed load balances total job duration across runners.

    When no duration manifest is active, fixed loads are NOT applied so that
    legacy heuristic scheduling behavior is preserved exactly.
    """
    if count >= 1 and durations_active:
        return {0: MOBILE_LAYOUT_DURATION_SECONDS}
    return None


def plan_shards(
    file_weights: Mapping[str, float],
    count: int,
    fixed_loads: Sequence[float] | Mapping[int, float] | None = None,
) -> tuple[frozenset[str], ...]:
    """Partition the files of ``file_weights`` into ``count`` balanced shards.

    Greedy longest-processing-time-first: heaviest file first, each one placed in
    the lightest shard so far. When ``fixed_loads`` is provided, shard load
    accumulators are initialized with these fixed costs, allowing the packer to
    balance total job completion time when some shards carry additional
    non-pytest work (e.g. mobile browser layout validation on shard 1).

    Ties break on the file key and then on the lowest shard index, so the plan
    depends only on the input and is identical in every parallel job. Returns
    one frozenset per shard, in shard order; shards may be empty when there
    are fewer files than shards.
    """
    if count < 1:
        raise ShardSpecError(f"shard count must be >= 1, got {count}")
    buckets: list[set[str]] = [set() for _ in range(count)]
    loads = [0.0] * count

    if fixed_loads is not None:
        if isinstance(fixed_loads, Mapping):
            for idx, cost in fixed_loads.items():
                if 0 <= idx < count and math.isfinite(cost) and cost > 0:
                    loads[idx] = float(cost)
        elif isinstance(fixed_loads, (list, tuple)):
            for idx, cost in enumerate(fixed_loads[:count]):
                if math.isfinite(cost) and cost > 0:
                    loads[idx] = float(cost)

    # Heaviest first, with the file key as a deterministic tie-break.
    ordered = sorted(file_weights.items(), key=lambda item: (-item[1], item[0]))
    for file_key, weight in ordered:
        target = min(range(count), key=lambda index: (loads[index], index))
        buckets[target].add(file_key)
        loads[target] += weight
    return tuple(frozenset(bucket) for bucket in buckets)


def shard_loads(
    file_weights: Mapping[str, float],
    plan: tuple[frozenset[str], ...],
    fixed_loads: Sequence[float] | Mapping[int, float] | None = None,
) -> tuple[float, ...]:
    """Total weight of each shard in ``plan``, including optional fixed loads."""
    base = [0.0] * len(plan)
    if fixed_loads is not None:
        if isinstance(fixed_loads, Mapping):
            for idx, cost in fixed_loads.items():
                if 0 <= idx < len(plan) and math.isfinite(cost) and cost > 0:
                    base[idx] = float(cost)
        elif isinstance(fixed_loads, (list, tuple)):
            for idx, cost in enumerate(fixed_loads[:len(plan)]):
                if math.isfinite(cost) and cost > 0:
                    base[idx] = float(cost)
    return tuple(
        base[i] + sum(file_weights[file_key] for file_key in bucket)
        for i, bucket in enumerate(plan)
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
