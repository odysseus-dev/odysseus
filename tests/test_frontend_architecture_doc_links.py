"""Relative links in the frontend-architecture docs point at files that exist.

The Notes inventories link every module they list; a module that is deleted
or renamed must leave those tables too, or the link silently breaks.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "frontend-architecture"
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def test_relative_links_resolve():
    broken = []
    for doc in sorted(DOCS.rglob("*.md")):
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert broken == []
