"""Skip gate for tests that drive Chromium through node scripts using the
``playwright`` npm package. The package is optional locally (Linux CI has it),
so detect its absence by resolving it from the repo root instead of by OS.
"""

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def playwright_available() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    try:
        result = subprocess.run(
            [node, "--input-type=module", "-e", "await import('playwright')"],
            cwd=ROOT, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


# CI installs the package with `npm ci`; never skip there, so a broken
# install fails loudly instead of silently dropping browser coverage.
requires_playwright = pytest.mark.skipif(
    not os.environ.get("CI") and not playwright_available(),
    reason="node 'playwright' npm package cannot be resolved from the repo root",
)
