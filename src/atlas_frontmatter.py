"""Atlas — YAML frontmatter + heading-section parsing for the Bases query engine.

Two pure helpers, shared by the query evaluator (``src/atlas_query.py``), the
HTTP routes, and the MCP server:

* ``parse_frontmatter(md)`` — pull a leading ``---\\n…\\n---`` YAML block off a
  note (Obsidian's "properties") and return ``(props, body)``. Tolerant: a
  missing or malformed block yields ``({}, original_md)`` rather than raising.
* ``extract_sections(md)`` — split a note on ATX headings into
  ``[{heading, level, body}]`` so queries can target "sections named todo".

Kept dependency-light (PyYAML, already a core requirement) and side-effect free
so both are trivial to unit-test.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import yaml

# A frontmatter block: --- on its own first line, then YAML, then a closing ---.
# DOTALL so the body spans newlines; anchored at string start.
_FM_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)

# ATX heading: 1–6 leading #, a space, then the text (trailing #'s optional).
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def parse_frontmatter(md: str) -> Tuple[Dict[str, Any], str]:
    """Return ``(properties, body)`` for a note.

    ``properties`` is the parsed YAML mapping (empty dict when there's no block
    or it doesn't parse to a mapping). ``body`` is the note minus the block.
    """
    if not md:
        return {}, md or ""
    m = _FM_RE.match(md)
    if not m:
        return {}, md
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, md
    if not isinstance(data, dict):
        return {}, md
    return data, md[m.end():]


def extract_sections(md: str) -> List[Dict[str, Any]]:
    """Split ``md`` into heading-delimited sections.

    Each section is ``{"heading", "level", "body"}`` where ``body`` is the text
    between this heading and the next (frontmatter stripped first). Content
    before the first heading is ignored — a section is defined by its heading,
    which is what "sections named X" queries operate on.
    """
    _, body = parse_frontmatter(md)
    matches = list(_HEADING_RE.finditer(body))
    sections: List[Dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append({
            "heading": m.group(2).strip(),
            "level": len(m.group(1)),
            "body": body[start:end].strip("\n"),
        })
    return sections
