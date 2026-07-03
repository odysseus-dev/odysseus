"""Regression: FASTEMBED_CACHE_DIR must tolerate a PRESENT-but-EMPTY
FASTEMBED_CACHE_PATH.

docker-compose.yml injects ``FASTEMBED_CACHE_PATH=${FASTEMBED_CACHE_PATH:-}``,
which sets the variable to ``""`` when the host has not defined it. The old
``os.getenv("FASTEMBED_CACHE_PATH", default)`` only used the default when the
variable was ABSENT, so an empty value made ``FASTEMBED_CACHE_DIR == ""`` →
``os.makedirs("")`` raised ``[Errno 2] No such file or directory: ''`` →
FastEmbed failed to initialise and every vector feature (RAG, semantic memory,
tool index) silently degraded on the default Docker stack.

These tests pin the fix: empty is treated like absent → use the DATA_DIR
default, while an explicit non-empty override is still honoured.
"""

from __future__ import annotations

import importlib
import os

import src.constants as constants


def _reload_with(monkeypatch, value):
    """Reload src.constants with FASTEMBED_CACHE_PATH set to ``value`` (or
    removed when ``value`` is None) and return the reloaded module."""
    if value is None:
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    else:
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", value)
    return importlib.reload(constants)


def _restore(monkeypatch):
    """Return the module to its env-default state so reloading it here does
    not leak a test-specific FASTEMBED_CACHE_DIR into other tests."""
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    importlib.reload(constants)


def test_empty_fastembed_cache_path_falls_back_to_default(monkeypatch):
    """The bug: an empty FASTEMBED_CACHE_PATH (exactly what Docker injects)
    must fall back to the DATA_DIR default, never the empty string."""
    try:
        mod = _reload_with(monkeypatch, "")
        assert mod.FASTEMBED_CACHE_DIR, "empty env must not yield an empty path"
        assert mod.FASTEMBED_CACHE_DIR == os.path.join(mod.DATA_DIR, "fastembed_cache")
    finally:
        _restore(monkeypatch)


def test_unset_fastembed_cache_path_uses_default(monkeypatch):
    """Sanity: an absent variable also resolves to the default."""
    try:
        mod = _reload_with(monkeypatch, None)
        assert mod.FASTEMBED_CACHE_DIR == os.path.join(mod.DATA_DIR, "fastembed_cache")
    finally:
        _restore(monkeypatch)


def test_explicit_fastembed_cache_path_is_respected(monkeypatch):
    """A real explicit override must still win — the fix only changes the
    empty-value handling, not the documented FASTEMBED_CACHE_PATH override."""
    custom = os.path.join("custom", "fastembed-cache")
    try:
        mod = _reload_with(monkeypatch, custom)
        assert mod.FASTEMBED_CACHE_DIR == custom
    finally:
        _restore(monkeypatch)
