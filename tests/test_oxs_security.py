"""Tests for OXS security helpers (hashing, AST parsing, ZIP safety).

Validates the supply-chain and parsing checks in
``core/extensions/security.py``. Every check is fail-closed — bad input
raises ``OXSSecurityError`` with a list of reasons.
"""

from __future__ import annotations

import os
import stat
import tempfile
import zipfile

import pytest

from core.extensions import (
    OXSSecurityError,
    ast_parse_safe,
    compute_extension_hashes,
    find_register_callable,
    sha256_of_bytes,
    sha256_of_file,
    verify_file_hashes,
    verify_zip_safe,
)


# ── sha256 helpers ────────────────────────────────────────────────────────


def test_sha256_of_bytes_known_value():
    # Empty input is the well-known constant.
    assert sha256_of_bytes(b"") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_sha256_of_bytes_hello():
    # "hello\n" → well-known constant.
    assert sha256_of_bytes(b"hello\n") == (
        "sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


def test_sha256_of_file_matches_bytes():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"some content")
        path = f.name
    try:
        assert sha256_of_file(path) == sha256_of_bytes(b"some content")
    finally:
        os.unlink(path)


def test_sha256_of_file_handles_large_file():
    """1 MB file: should stream, not load all in memory."""
    payload = b"a" * (1024 * 1024)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(payload)
        path = f.name
    try:
        assert sha256_of_file(path) == sha256_of_bytes(payload)
    finally:
        os.unlink(path)


# ── compute_extension_hashes ──────────────────────────────────────────────


def test_compute_extension_hashes_walks_directory():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a"), "w").write("a")
        open(os.path.join(d, "b"), "w").write("b")
        os.makedirs(os.path.join(d, "sub"))
        open(os.path.join(d, "sub", "c"), "w").write("c")

        hashes = compute_extension_hashes(d)

        assert "a" in hashes
        assert "b" in hashes
        assert "sub/c" in hashes
        assert hashes["a"] == sha256_of_bytes(b"a")
        assert hashes["sub/c"] == sha256_of_bytes(b"c")


def test_compute_extension_hashes_excludes_manifest_by_default():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "plugin.py"), "w").write("# plugin")
        manifest = os.path.join(d, "extension.json")
        open(manifest, "w").write("{}")
        hashes = compute_extension_hashes(d, manifest_filename="extension.json")
        assert "extension.json" not in hashes
        assert "plugin.py" in hashes


def test_compute_extension_hashes_skips_hidden_and_pycache():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "good.py"), "w").write("x")
        os.makedirs(os.path.join(d, ".hidden"))
        open(os.path.join(d, ".hidden", "bad.py"), "w").write("x")
        os.makedirs(os.path.join(d, "__pycache__"))
        open(os.path.join(d, "__pycache__", "x.pyc"), "w").write("x")
        hashes = compute_extension_hashes(d)
        assert "good.py" in hashes
        assert all(".hidden" not in k for k in hashes)
        assert all("__pycache__" not in k for k in hashes)


def test_compute_extension_hashes_uses_forward_slashes():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "ui"))
        open(os.path.join(d, "ui", "index.html"), "w").write("x")
        hashes = compute_extension_hashes(d)
        # On POSIX the OS separator is "/"; on Windows it would be "\\" if
        # we forgot to normalize. The function always returns forward slashes.
        for key in hashes:
            assert "\\" not in key


# ── verify_file_hashes ────────────────────────────────────────────────────


def test_verify_file_hashes_ok():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "plugin.py"), "w").write("# plugin")
        declared = compute_extension_hashes(d)
        verify_file_hashes(d, declared)  # does not raise


def test_verify_file_hashes_rejects_mismatch():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "plugin.py"), "w").write("# plugin")
        declared = compute_extension_hashes(d)
        declared["plugin.py"] = "sha256:" + "0" * 64
        with pytest.raises(OXSSecurityError) as exc_info:
            verify_file_hashes(d, declared)
        assert any("hash mismatch" in e for e in exc_info.value.errors)


def test_verify_file_hashes_rejects_missing_file():
    with tempfile.TemporaryDirectory() as d:
        declared = {"plugin.py": "sha256:" + "a" * 64}
        with pytest.raises(OXSSecurityError) as exc_info:
            verify_file_hashes(d, declared)
        assert any("missing on disk" in e for e in exc_info.value.errors)


def test_verify_file_hashes_rejects_traversal_in_declared_path():
    with tempfile.TemporaryDirectory() as d:
        declared = {"../escape.txt": "sha256:" + "a" * 64}
        with pytest.raises(OXSSecurityError) as exc_info:
            verify_file_hashes(d, declared)
        assert any("escapes root" in e for e in exc_info.value.errors)


def test_verify_file_hashes_rejects_absolute_declared_path():
    with tempfile.TemporaryDirectory() as d:
        declared = {"/etc/passwd": "sha256:" + "a" * 64}
        with pytest.raises(OXSSecurityError) as exc_info:
            verify_file_hashes(d, declared)
        assert any("escapes root" in e or "outside root" in e for e in exc_info.value.errors)


def test_verify_file_hashes_rejects_malformed_hash():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "plugin.py"), "w").write("# plugin")
        with pytest.raises(OXSSecurityError) as exc_info:
            verify_file_hashes(d, {"plugin.py": "md5:abc"})
        assert any("sha256:" in e for e in exc_info.value.errors)


def test_verify_file_hashes_extra_disk_files_ok():
    """Files on disk NOT in declared are allowed (logs, generated, etc)."""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "plugin.py"), "w").write("# plugin")
        open(os.path.join(d, "extra.log"), "w").write("info")
        declared = compute_extension_hashes(d)
        del declared["extra.log"]
        verify_file_hashes(d, declared)  # no error


# ── verify_zip_safe ───────────────────────────────────────────────────────


def _make_zip(tmpdir, entries):
    """Helper: build a ZIP file with the given (name, content, mode?) tuples."""
    zpath = os.path.join(tmpdir, "test.zip")
    with zipfile.ZipFile(zpath, "w") as zf:
        for entry in entries:
            if len(entry) == 2:
                name, content = entry
                zf.writestr(name, content)
            else:
                name, content, mode = entry
                zi = zipfile.ZipInfo(name)
                zi.external_attr = (mode & 0xFFFF) << 16
                zf.writestr(zi, content)
    return zpath


def test_verify_zip_safe_accepts_normal_archive():
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("plugin.py", "# ok"),
            ("ui/index.html", "<html/>"),
        ])
        with zipfile.ZipFile(zpath) as zf:
            verify_zip_safe(zf)


def test_verify_zip_safe_rejects_traversal():
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("../escape.txt", "x"),
        ])
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf)
            assert any("traversal" in e for e in exc_info.value.errors)


def test_verify_zip_safe_rejects_absolute_path():
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("/etc/passwd", "x"),
        ])
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf)
            assert any("absolute" in e for e in exc_info.value.errors)


def test_verify_zip_safe_rejects_backslash_path():
    """Windows-style backslash paths must be refused outright."""
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("ui\\index.html", "x"),
        ])
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf)
            assert any("intermediate empty segment" in e or "unsafe path" in e
                       for e in exc_info.value.errors)


def test_verify_zip_safe_rejects_drive_letter():
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("C:file.txt", "x"),
        ])
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf)
            assert any("drive letter" in e for e in exc_info.value.errors)


def test_verify_zip_safe_rejects_symlink():
    """Symlink member (mode S_ISLNK) must be refused."""
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("plugin.py", "x", 0o120000),  # S_IFLNK
        ])
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf)
            assert any("symlink" in e for e in exc_info.value.errors)


def test_verify_zip_safe_rejects_device_file():
    with tempfile.TemporaryDirectory() as d:
        zpath = _make_zip(d, [
            ("extension.json", "{}"),
            ("plugin.py", "x", 0o020000),  # S_IFCHR
        ])
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError):
                verify_zip_safe(zf)


def test_verify_zip_safe_rejects_too_many_members():
    with tempfile.TemporaryDirectory() as d:
        zpath = os.path.join(d, "bomb.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            # Exceed the default cap (10_000) cheaply.
            for i in range(10_001):
                zf.writestr(f"f{i}.txt", "x")
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf, max_members=100)
            assert any("max_members" in e or "max allowed" in e for e in exc_info.value.errors)


def test_verify_zip_safe_rejects_oversized_member():
    with tempfile.TemporaryDirectory() as d:
        zpath = os.path.join(d, "big.zip")
        big = b"x" * (10 * 1024 * 1024)
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("big.bin", big)
        with zipfile.ZipFile(zpath) as zf:
            with pytest.raises(OXSSecurityError) as exc_info:
                verify_zip_safe(zf, max_member_bytes=1024)
            assert any("max" in e for e in exc_info.value.errors)


# ── ast_parse_safe ────────────────────────────────────────────────────────


def test_ast_parse_safe_accepts_clean_source():
    tree = ast_parse_safe("def register(host):\n    pass\n")
    assert tree is not None


def test_ast_parse_safe_rejects_exec_call():
    with pytest.raises(OXSSecurityError) as exc_info:
        ast_parse_safe("def register(host):\n    exec('print(1)')\n")
    assert any("exec" in e for e in exc_info.value.errors)


def test_ast_parse_safe_rejects_eval_call():
    with pytest.raises(OXSSecurityError) as exc_info:
        ast_parse_safe("def register(host):\n    return eval('1+1')\n")
    assert any("eval" in e for e in exc_info.value.errors)


def test_ast_parse_safe_rejects_compile_call():
    with pytest.raises(OXSSecurityError):
        ast_parse_safe("def register(host):\n    compile('x', '<s>', 'exec')\n")


def test_ast_parse_safe_rejects_dunder_import():
    with pytest.raises(OXSSecurityError):
        ast_parse_safe("def register(host):\n    return __import__('os')\n")


def test_ast_parse_safe_rejects_syntax_error():
    with pytest.raises(OXSSecurityError):
        ast_parse_safe("def register(host:\n    pass\n")


def test_ast_parse_safe_rejects_invalid_utf8():
    with pytest.raises(OXSSecurityError):
        ast_parse_safe(b"\xff\xfe not utf-8")


def test_ast_parse_safe_accepts_bytes():
    tree = ast_parse_safe(b"def register(host):\n    pass\n")
    assert tree is not None


def test_find_register_callable_returns_name():
    tree = ast_parse_safe("def register(host):\n    pass\n")
    assert find_register_callable(tree) == "register"


def test_find_register_callable_returns_none_if_missing():
    tree = ast_parse_safe("def setup(host):\n    pass\n")
    assert find_register_callable(tree, "register") is None


def test_find_register_callable_returns_none_if_wrong_arity():
    """``register`` must take exactly one positional argument."""
    tree = ast_parse_safe("def register(host, extra):\n    pass\n")
    assert find_register_callable(tree) is None


def test_find_register_callable_ignores_lambda():
    """Lambdas assigned to ``register`` are not allowed."""
    tree = ast_parse_safe("register = lambda host: None\n")
    assert find_register_callable(tree) is None


def test_find_register_callable_ignores_nested_def():
    """``register`` must be top-level, not nested inside another function."""
    tree = ast_parse_safe(
        "def outer():\n"
        "    def register(host):\n"
        "        pass\n"
    )
    assert find_register_callable(tree) is None