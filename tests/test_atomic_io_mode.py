"""Regression: atomic_write_json mode parameter applies 0o600 on POSIX."""
import os
import stat
import pytest
from core.atomic_io import atomic_write_json

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")

def test_atomic_write_json_applies_mode(tmp_path):
    target = str(tmp_path / "secret.json")
    atomic_write_json(target, {"x": 1}, mode=0o600)
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"

def test_atomic_write_json_no_mode_leaves_default(tmp_path):
    """Without mode= the file gets OS umask permissions — regression guard."""
    target = str(tmp_path / "open.json")
    atomic_write_json(target, {"x": 1})
    # Just assert the file exists and is readable — do not assert a specific mode
    # since umask varies. The point is mode=None does not raise.
    assert os.path.exists(target)

def test_atomic_write_json_mode_overwrite(tmp_path):
    """Overwriting an existing file should re-apply the mode."""
    target = str(tmp_path / "rewrite.json")
    atomic_write_json(target, {"v": 1}, mode=0o600)
    os.chmod(target, 0o644)  # widen
    atomic_write_json(target, {"v": 2}, mode=0o600)
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, f"Expected 0o600 after overwrite, got 0o{mode:o}"
