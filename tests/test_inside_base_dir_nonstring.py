"""Regression: inside_base_dir must fail closed on a non-string input.

The `os.path.realpath(path)` calls run before the try/except (which only wraps
commonpath), so a None / non-string path raised TypeError out of this
path-safety check instead of returning False.
"""
from src.app_helpers import inside_base_dir


def test_non_string_fails_closed():
    assert inside_base_dir("/tmp", None) is False
    assert inside_base_dir("/tmp", 123) is False
    assert inside_base_dir(None, "/tmp/x") is False


def test_real_containment_still_works(tmp_path):
    base = str(tmp_path)
    assert inside_base_dir(base, str(tmp_path / "a.txt")) is True
    assert inside_base_dir(base, "/etc/passwd") is False
