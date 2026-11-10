"""No two @keyframes may share a name across the app's stylesheets.

Duplicate names resolve last-wins across the whole cascade, so an earlier
definition is dead code that still looks live at its call site. Two of them
existed and differed from the definition that actually won, which also blocked
moving either animation during the stylesheet decomposition: relocating one
changes which is last and therefore changes rendered behaviour.
"""

import collections
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _stylesheets() -> list[Path]:
    """App-owned stylesheets, in no particular order. Vendored CSS is excluded."""
    sheets = [STATIC / "style.css"]
    sheets.extend(sorted((STATIC / "css").glob("*.css")))
    return [s for s in sheets if s.exists()]


def _keyframe_names(text: str) -> list[str]:
    without_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.findall(
        r"@(?:-webkit-)?keyframes\s+([A-Za-z0-9_-]+)\s*\{", without_comments
    )


def test_app_stylesheets_exist() -> None:
    """Guard the guard: a rename must not turn this file into a no-op."""
    assert _stylesheets(), "no app stylesheets found to check"


def test_no_keyframes_name_is_declared_twice() -> None:
    counts: collections.Counter[str] = collections.Counter()
    where: dict[str, list[str]] = collections.defaultdict(list)

    for sheet in _stylesheets():
        for name in _keyframe_names(sheet.read_text(encoding="utf-8")):
            counts[name] += 1
            where[name].append(sheet.relative_to(ROOT).as_posix())

    duplicates = {name: where[name] for name, n in counts.items() if n > 1}

    assert duplicates == {}, (
        "duplicate @keyframes names resolve last-wins, so every definition but "
        f"the last is dead: {duplicates}"
    )
