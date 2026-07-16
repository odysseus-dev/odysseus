"""Load the runtime CSS bundle the browser actually sees.

The stylesheet was split from a single ``static/style.css`` into modular files
under ``static/css/`` that are stitched together at load time via ``@import``
rules in ``static/css/index.css``. Tests that assert on CSS need the same
concatenated text the browser resolves, in import order — this helper expands
the ``@import`` chain and returns that bundle.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

# Matches:  @import url("/static/css/base/tokens.css");
# capturing the (optional) quote char and the url in between.
_IMPORT_RE = re.compile(r"""^\s*@import\s+url\((['"]?)([^)'"]+)\1\)\s*;\s*$""")


def _expand_css_file(path, seen):
    """Return the text of ``path`` with any ``@import`` rules expanded inline."""
    path = path.resolve()
    if path in seen:
        # Guard against import cycles / double includes.
        return ""
    seen.add(path)

    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _IMPORT_RE.match(line)
        if m:
            url = m.group(2).strip()
            if url.startswith("/"):
                # Absolute web path, e.g. /static/css/base/tokens.css
                target = ROOT / url.lstrip("/")
            else:
                # Relative to the importing file.
                target = path.parent / url
            out.append(_expand_css_file(target, seen))
        else:
            out.append(line)
    return "\n".join(out)


def load_runtime_css_text():
    """Return the runtime CSS bundle in import order from static/css/index.css."""
    index = ROOT / "static" / "css" / "index.css"
    return _expand_css_file(index, set())
