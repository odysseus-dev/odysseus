"""Skip marker for tests that need to create symlinks.

On native Windows, creating a symlink needs SeCreateSymbolicLinkPrivilege
(Developer Mode or an elevated account); without it ``os.symlink`` raises
``OSError: [WinError 1314]``. The probe runs once and is cached.
"""

import os
import tempfile
from functools import lru_cache

import pytest


@lru_cache(maxsize=1)
def can_symlink() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "target")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        try:
            os.symlink(target, os.path.join(tmp, "link"))
        except (OSError, NotImplementedError):
            return False
    return True


requires_symlinks = pytest.mark.skipif(
    not can_symlink(),
    reason="symlinks need a privilege on this Windows account",
)
