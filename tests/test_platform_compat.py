"""Windows embedding-cache repair.

A HuggingFace model cache populated by a *different* OS — e.g. the Linux Docker
container writing into a bind-mounted ``data/`` dir that a native Windows run
later reads — stores ``snapshots/<rev>/<file>`` as POSIX symlinks into
``blobs/``. Read back on Windows these surface as reparse points with the
WSL/Linux symlink tag (0xA000001D) that Windows cannot follow: ``open()`` raises
WinError 1920 / OSError(22) and fastembed loads a zero-byte model and dies.
Crucially ``os.path.islink()`` returns **False** for them, so the original
broken-symlink check (``islink and not exists``) never caught them.

``purge_unreadable_hf_cache`` must key off *unresolvable* entries — present on
disk (``lexists``) but not resolvable (``not exists``) — which covers both a
dangling Windows symlink and a foreign reparse point, while leaving a healthy
cache of real files (native copy mode) untouched.

A dangling symlink reproduces the exact ``lexists and not exists`` code path on
any OS, so these tests pin the behavior portably without needing a real
LX-symlink reparse point.
"""
import os

import pytest

from core import platform_compat


def _make_model_dir(cache_dir, repo="models--qdrant--all-MiniLM-L6-v2-onnx", rev="rev0"):
    snap = os.path.join(cache_dir, repo, "snapshots", rev)
    blobs = os.path.join(cache_dir, repo, "blobs")
    os.makedirs(snap)
    os.makedirs(blobs)
    return snap, blobs, os.path.join(cache_dir, repo)


def test_purge_removes_unresolvable_model_dir(tmp_path):
    """A model whose .onnx is present-but-unresolvable gets its models-- dir dropped."""
    cache = str(tmp_path)
    snap, blobs, root = _make_model_dir(cache)
    onnx = os.path.join(snap, "model.onnx")
    try:
        os.symlink(os.path.join(blobs, "missing-blob"), onnx)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is not permitted on this host")

    # This is the exact state that fooled the old islink() check.
    assert os.path.lexists(onnx) and not os.path.exists(onnx)
    assert not os.path.islink(onnx) or True  # islink may be False (foreign reparse)

    removed = platform_compat.purge_unreadable_hf_cache(cache)

    assert removed == [root]
    assert not os.path.exists(root)


def test_purge_keeps_healthy_real_file_cache(tmp_path):
    """A cache of real files (native copy mode) must never be cleared."""
    cache = str(tmp_path)
    snap, _blobs, root = _make_model_dir(cache)
    with open(os.path.join(snap, "model.onnx"), "wb") as fh:
        fh.write(b"\x00" * 32)

    removed = platform_compat.purge_unreadable_hf_cache(cache)

    assert removed == []
    assert os.path.exists(root)


def test_purge_missing_dir_is_noop(tmp_path):
    """First-ever run (no cache dir yet) is a safe no-op."""
    assert platform_compat.purge_unreadable_hf_cache(str(tmp_path / "nope")) == []
