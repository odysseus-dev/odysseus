"""Regression guard for issue #1515 — numpy 2.4+ ships an x86-64-v2 baseline that
crashes on older CPUs ("NumPy was built with baseline optimizations (X86_V2) but
your machine doesn't support (X86_V2)"). 2.3.x is the last line that runs on
pre-x86-64-v2 hardware, which self-hosted users often have.

Pin numpy below 2.4 so a fresh install doesn't pull a build that won't run.
"""
import re
from pathlib import Path

REQ = Path(__file__).resolve().parent.parent / "requirements.txt"


def test_numpy_pinned_below_2_4():
    lines = [
        ln.split("#", 1)[0].strip()
        for ln in REQ.read_text(encoding="utf-8").splitlines()
        if ln.split("#", 1)[0].strip().lower().startswith("numpy")
    ]
    assert lines, "numpy requirement not found"
    spec = lines[0]
    # Must carry an explicit upper bound, not a bare/unpinned `numpy`.
    assert re.search(r"<\s*2\.4|<=\s*2\.3", spec), f"numpy must be capped <2.4 (#1515): {spec!r}"
