"""Regression tests for stale ProcessResource during authority intersection.

Covers:
1. Parent observes process → process exits → child intersection does not crash.
2. Stale process disappears from resulting child authority.
3. Stale parent cannot be renewed by a fresh/replacement process.
4. PID reuse/replacement remains rejected.
5. Child-side stale observation is handled conservatively.
6. Valid live identical observations still intersect correctly.
"""
import pytest
from dataclasses import dataclass

from src.agent_runtime.process_resources import intersect_observed
from src.agent_runtime.resources import ResourceIdentityError


@dataclass(frozen=True)
class _FakeResource:
    """Lightweight stand-in for ProcessResource/BackgroundJobResource in
    intersection tests. Equality is by (pid, token) so we can verify
    identity-based matching, while ``live`` controls whether validate raises.
    """
    pid: int
    token: str
    live: bool = True

    def __eq__(self, other):
        return isinstance(other, _FakeResource) and (self.pid, self.token) == (other.pid, other.token)

    def __hash__(self):
        return hash((self.pid, self.token))


def _validate(resource):
    """Mirrors ProcessResource.validate() semantics."""
    if not resource.live:
        raise ResourceIdentityError("Process resource is stale or unverifiable")


# 1. Parent observes process → process exits → child intersection does not crash.
def test_stale_parent_process_does_not_crash_intersection():
    stale = _FakeResource(pid=1000, token="tok-1", live=False)
    child_copy = _FakeResource(pid=1000, token="tok-1", live=False)
    result = intersect_observed((stale,), (child_copy,), _validate)
    # Must not raise; stale resources are conservatively excluded.
    assert result == ()


# 2. Stale process disappears from resulting child authority.
def test_stale_process_excluded_from_intersection_result():
    live = _FakeResource(pid=2000, token="tok-2", live=True)
    stale = _FakeResource(pid=3000, token="tok-3", live=False)
    child_live = _FakeResource(pid=2000, token="tok-2", live=True)
    child_stale = _FakeResource(pid=3000, token="tok-3", live=False)
    result = intersect_observed((live, stale), (child_live, child_stale), _validate)
    assert len(result) == 1
    assert result[0].pid == 2000


# 3. Stale parent cannot be renewed by a fresh/replacement process.
def test_stale_parent_not_renewed_by_fresh_child():
    stale_parent = _FakeResource(pid=4000, token="tok-4", live=False)
    fresh_child = _FakeResource(pid=4000, token="tok-4-new", live=True)
    result = intersect_observed((stale_parent,), (fresh_child,), _validate)
    # Parent is stale → excluded before equality check.
    assert result == ()


# 4. PID reuse/replacement remains rejected.
def test_pid_reuse_rejected():
    """A replacement process with the same PID but different token is never equal."""
    original = _FakeResource(pid=5000, token="tok-original", live=True)
    replacement = _FakeResource(pid=5000, token="tok-replacement", live=True)
    result = intersect_observed((original,), (replacement,), _validate)
    # Different identity → not equal → not in result.
    assert result == ()


# 5. Child-side stale observation is handled conservatively.
def test_child_side_stale_excluded():
    live_parent = _FakeResource(pid=6000, token="tok-6", live=True)
    stale_child = _FakeResource(pid=6000, token="tok-6", live=False)
    result = intersect_observed((live_parent,), (stale_child,), _validate)
    # Child side is stale → not in live_child set → excluded.
    assert result == ()


# 6. Valid live identical observations still intersect correctly.
def test_live_identical_observations_intersect():
    parent = _FakeResource(pid=7000, token="tok-7", live=True)
    child = _FakeResource(pid=7000, token="tok-7", live=True)
    result = intersect_observed((parent,), (child,), _validate)
    assert len(result) == 1
    assert result[0].pid == 7000
    assert result[0].token == "tok-7"


# Additional: multiple live resources intersect correctly preserving order.
def test_multiple_live_resources_intersect():
    p1 = _FakeResource(pid=8000, token="tok-8a", live=True)
    p2 = _FakeResource(pid=8001, token="tok-8b", live=True)
    c1 = _FakeResource(pid=8000, token="tok-8a", live=True)
    c2 = _FakeResource(pid=8001, token="tok-8b", live=True)
    result = intersect_observed((p1, p2), (c1, c2), _validate)
    assert len(result) == 2
    assert result[0].pid == 8000
    assert result[1].pid == 8001


# Additional: mixed stale/live across both sides.
def test_mixed_stale_live_across_both_sides():
    p_live = _FakeResource(pid=9000, token="tok-9a", live=True)
    p_stale = _FakeResource(pid=9001, token="tok-9b", live=False)
    c_live = _FakeResource(pid=9000, token="tok-9a", live=True)
    c_stale = _FakeResource(pid=9001, token="tok-9b", live=False)
    result = intersect_observed((p_live, p_stale), (c_live, c_stale), _validate)
    assert len(result) == 1
    assert result[0].pid == 9000


# Edge: empty inputs produce empty output.
def test_empty_intersection():
    assert intersect_observed((), (), _validate) == ()
