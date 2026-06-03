"""Regression: _lookup_bandwidth must tolerate a non-string gpu_name.

It guarded only falsy values; a truthy non-string (e.g. a number from a
malformed hardware probe) reached `gpu_name.lower()` and raised AttributeError.
"""
from services.hwfit.fit import _lookup_bandwidth


def test_non_string_returns_none():
    assert _lookup_bandwidth(123) is None
    assert _lookup_bandwidth(["x"]) is None
    assert _lookup_bandwidth(None) is None


def test_known_gpu_resolves():
    assert _lookup_bandwidth("NVIDIA GeForce RTX 4090") is not None
