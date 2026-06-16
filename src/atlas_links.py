"""Atlas — wikilink / tag parsing and graph construction.

Shared core used by ``routes/atlas_routes.py`` (the HTTP API), the graph/
backlinks endpoints, and the ``manage_atlas`` agent tool, so all three agree on
exactly how ``[[wikilinks]]``, ``![[embeds]]`` and ``#tags`` are interpreted.

The parsing mirrors Obsidian's conventions closely enough to be compatible with
a real vault:

* ``[[Note]]``, ``[[Note|alias]]``, ``[[Note#heading]]`` — internal links.
* ``![[Note]]`` / ``![[image.png]]`` — embeds (counted as links in the graph).
* ``#tag``, ``#parent/child`` — tags (purely-numeric ``#123`` is NOT a tag).
* Link resolution uses Obsidian's "shortest unique path": an exact relative
  path wins, otherwise a unique basename match, otherwise the link is
  *unresolved* and surfaces as a placeholder node in the graph.

Everything here is pure (no I/O), so it is cheap to unit-test in isolation.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

# A fenced code block (``` or ~~~) or an inline `code span`. Stripped before
# scanning so a ``[[link]]`` or ``#tag`` shown inside an example doesn't pollute
# the real link graph.
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Capture an optional leading `!` (embed marker) and the inner target of a
# wikilink. The inner part excludes brackets so nested/broken brackets don't
# swallow the rest of the line.
_WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]]+?)\]\]")

# A tag: `#` followed by word/slash/dash chars, anchored to start-of-line or
# whitespace so `# Heading` (space after #) and `url#frag` are not tags.
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9_][\w/-]*)")

# First ATX/Setext-ish heading line, used to derive a human title.
_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def strip_code(md: str) -> str:
    """Blank out fenced and inline code so it isn't scanned for links/tags."""
    md = _FENCE_RE.sub("", md)
    md = _INLINE_CODE_RE.sub("", md)
    return md


def _link_target(inner: str) -> str:
    """Reduce a wikilink body to just the note/file part.

    ``Note|alias`` -> ``Note``; ``Note#heading`` -> ``Note``; a same-file
    ``#heading`` link (empty note part) -> ``""`` (caller ignores it).
    """
    target = inner.split("|", 1)[0]
    target = target.split("#", 1)[0]
    return target.strip()


def parse_links(md: str) -> Dict[str, List[str]]:
    """Parse a note body into its outgoing links, embeds and tags.

    Returns ``{"wikilinks": [...], "embeds": [...], "tags": [...]}`` with each
    list de-duplicated but order-preserving. ``wikilinks`` holds the raw target
    names (not yet resolved to paths); ``embeds`` are the targets of ``![[ ]]``;
    ``tags`` are without the leading ``#``.
    """
    body = strip_code(md or "")

    wikilinks: List[str] = []
    embeds: List[str] = []
    for bang, inner in _WIKILINK_RE.findall(body):
        target = _link_target(inner)
        if not target:
            continue  # same-file heading link, e.g. [[#section]]
        bucket = embeds if bang else wikilinks
        if target not in bucket:
            bucket.append(target)

    tags: List[str] = []
    for raw in _TAG_RE.findall(body):
        tag = raw.rstrip("/")
        # Obsidian: a tag must contain at least one non-digit character.
        if not tag or tag.replace("/", "").isdigit():
            continue
        if tag not in tags:
            tags.append(tag)

    return {"wikilinks": wikilinks, "embeds": embeds, "tags": tags}


def note_title(relpath: str, md: str = "") -> str:
    """Human title for a note: its first ``# H1`` if present, else the stem."""
    m = _H1_RE.search(md or "")
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(relpath))[0]


def _norm(name: str) -> str:
    """Normalise a link target for matching: drop a trailing ``.md``, lowercase,
    use forward slashes."""
    name = name.replace("\\", "/").strip().lstrip("/")
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.lower()


def resolve_link(name: str, all_relpaths: List[str]) -> Optional[str]:
    """Resolve a wikilink target to an actual note relpath (Obsidian-style).

    Resolution order:
      1. Exact relative-path match (with or without ``.md``).
      2. Unique basename match (the note's filename without extension).
    Returns ``None`` when the target matches nothing (an *unresolved* link).
    """
    if not name:
        return None
    target = _norm(name)

    # 1. Exact path match.
    for rel in all_relpaths:
        if _norm(rel) == target:
            return rel

    # 2. Basename match (the part after the last slash).
    base = target.rsplit("/", 1)[-1]
    matches = [rel for rel in all_relpaths
               if _norm(os.path.basename(rel)) == base]
    if len(matches) == 1:
        return matches[0]
    if matches:
        # Ambiguous basename: prefer the shortest path, mirroring Obsidian's
        # "closest" heuristic well enough for a deterministic, stable choice.
        return sorted(matches, key=lambda r: (r.count("/"), len(r)))[0]
    return None


def build_graph(notes: Dict[str, str]) -> Dict[str, list]:
    """Build a link graph from ``{relpath: markdown}``.

    Returns ``{"nodes": [...], "links": [...]}`` where each node is
    ``{"id", "title", "tags", "missing"}`` and each link is
    ``{"source", "target"}`` (both relpaths). Links whose target doesn't resolve
    to a real note become a ``missing`` placeholder node, so the graph shows
    "ghost" notes you've linked to but not created yet — exactly like Obsidian.
    """
    all_relpaths = list(notes.keys())
    nodes: Dict[str, dict] = {}
    links: List[dict] = []
    seen_edges = set()

    for rel, md in notes.items():
        parsed = parse_links(md)
        nodes[rel] = {
            "id": rel,
            "title": note_title(rel, md),
            "tags": parsed["tags"],
            "missing": False,
        }

    for rel, md in notes.items():
        parsed = parse_links(md)
        for target in parsed["wikilinks"] + parsed["embeds"]:
            resolved = resolve_link(target, all_relpaths)
            if resolved is None:
                # Ghost node for a link to a not-yet-created note.
                ghost_id = f"::missing::{_norm(target)}"
                if ghost_id not in nodes:
                    nodes[ghost_id] = {
                        "id": ghost_id,
                        "title": target,
                        "tags": [],
                        "missing": True,
                    }
                resolved = ghost_id
            if resolved == rel:
                continue  # ignore self-links
            edge = (rel, resolved)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            links.append({"source": rel, "target": resolved})

    return {"nodes": list(nodes.values()), "links": links}


def backlinks(target_relpath: str, notes: Dict[str, str]) -> List[str]:
    """Relpaths of notes that link TO ``target_relpath``."""
    all_relpaths = list(notes.keys())
    out: List[str] = []
    for rel, md in notes.items():
        if rel == target_relpath:
            continue
        parsed = parse_links(md)
        for name in parsed["wikilinks"] + parsed["embeds"]:
            if resolve_link(name, all_relpaths) == target_relpath:
                out.append(rel)
                break
    return out
