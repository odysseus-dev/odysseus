"""SKILL.md parser & writer.

Reads/writes a single skill from a `SKILL.md` file with YAML frontmatter
and a structured markdown body. Inspired by Hermes' skills format
(https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).

Frontmatter shape (YAML):

    ---
    name: open-pr-from-branch
    description: One-line summary surfaced in the skills index.
    version: 1.0.0
    category: dev
    tags: [git, github]
    platforms: [linux, macos]            # optional
    requires_toolsets: []                # optional
    fallback_for_toolsets: []            # optional
    status: published                    # draft | published
    confidence: 0.8                      # 0..1
    source: learned                      # learned | taught | imported
    teacher_model: claude-opus-4-7       # optional
    created: 2026-05-09T21:43:00Z
    ---

Body sections (any subset; rendered as headings):

    ## When to Use
    Trigger conditions in plain English.

    ## Procedure
    1. First step
    2. Second step

    ## Pitfalls
    - Common failure mode + how to recover

    ## Verification
    - How to confirm success

    Anything else (raw paragraphs after the last known section) is preserved
    in `body_extra` and round-trips on save.

Usage counters (`uses`, `last_used`) live in a sidecar `_usage.json` keyed
by skill name, so the SKILL.md file doesn't churn on every retrieval.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "skill") -> str:
    """Convert a free-form title to a kebab-case slug suitable for a directory
    name. Strips non-alphanumerics, collapses runs, trims leading/trailing
    dashes. Caps at 60 chars."""
    s = str(text or "").strip().lower()
    s = _SLUG_RE.sub("-", s)
    s = s.strip("-")
    return (s or fallback)[:60]


# ---------------------------------------------------------------------------
# Frontmatter (minimal YAML — we don't pull in PyYAML for one feature)
# ---------------------------------------------------------------------------

# We accept a tiny subset of YAML: scalar `key: value`, inline lists `[a, b]`,
# and block lists with `-`. That covers everything in our schema and avoids
# a new dependency.

_FM_KEY_RE = re.compile(r"^([a-z_][a-z0-9_]*):\s*(.*)$", re.IGNORECASE)
_FM_BLOCK_LIST_RE = re.compile(r"^\s*-\s*(.*)$")


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw == "":
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p) for p in _split_top_level(inner, ",")]
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    if raw.lower() in ("null", "none", "~"):
        return None
    if (raw[0] == raw[-1]) and raw[0] in ("'", '"'):
        return raw[1:-1]
    # Try number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    return raw


def _split_top_level(s: str, sep: str) -> List[str]:
    """Split `s` on `sep` ignoring separators inside [] or quotes."""
    out, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


def parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Pull the YAML frontmatter out of a SKILL.md and return (fm, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_text = text[3:end].lstrip("\n")
    body = text[end + 4:].lstrip("\n")
    fm: Dict[str, Any] = {}
    pending_key: Optional[str] = None
    for line in fm_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _FM_KEY_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2)
            if val.strip() == "":
                pending_key = key
                fm[key] = []
            else:
                fm[key] = _parse_scalar(val)
                pending_key = None
            continue
        m2 = _FM_BLOCK_LIST_RE.match(line)
        if m2 and pending_key:
            existing = fm.get(pending_key)
            if not isinstance(existing, list):
                fm[pending_key] = []
            fm[pending_key].append(_parse_scalar(m2.group(1)))
    return fm, body


def _emit_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_emit_scalar(x) for x in v) + "]"
    s = str(v)
    if any(c in s for c in (":", "#", "\n", "[", "]", "{", "}", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@")):
        return json.dumps(s)
    return s


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    return [str(v)]


def _as_float(v: Any, default: float = 0.8) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def emit_frontmatter(fm: Dict[str, Any]) -> str:
    lines = []
    for k, v in fm.items():
        if v is None or v == [] or v == "":
            continue
        lines.append(f"{k}: {_emit_scalar(v)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill body sections
# ---------------------------------------------------------------------------

_KNOWN_SECTIONS = ("when_to_use", "procedure", "pitfalls", "verification")
_HEADING_TO_KEY = {
    "when to use": "when_to_use",
    "procedure": "procedure",
    "steps": "procedure",
    "pitfalls": "pitfalls",
    "verification": "verification",
}
_KEY_TO_HEADING = {
    "when_to_use": "When to Use",
    "procedure": "Procedure",
    "pitfalls": "Pitfalls",
    "verification": "Verification",
}


def parse_body(body: str) -> Dict[str, Any]:
    """Split a SKILL.md body into known sections.

    Returns:
        {
            "when_to_use": str,
            "procedure":   list[str],   # numbered/bulleted lines
            "pitfalls":    list[str],
            "verification": list[str],
            "body_extra":  str,         # anything not under a known heading
        }
    """
    out = {k: ([] if k != "when_to_use" else "") for k in _KNOWN_SECTIONS}
    out["body_extra"] = ""
    if not body or not body.strip():
        return out

    sections: List[tuple[Optional[str], List[str]]] = [(None, [])]
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            heading = m.group(1).strip().lower()
            key = _HEADING_TO_KEY.get(heading)
            sections.append((key, []))
            continue
        sections[-1][1].append(line)

    for key, lines in sections:
        text = "\n".join(lines).strip("\n")
        if key is None:
            extras = text.strip()
            if extras:
                out["body_extra"] = (out["body_extra"] + "\n\n" + extras).strip()
            continue
        if key == "when_to_use":
            out["when_to_use"] = text.strip()
        else:
            out[key] = _parse_list_lines(text)
    return out


def _parse_list_lines(text: str) -> List[str]:
    """Pull bullet/numbered lines out of a section body. Plain paragraphs are
    treated as a single entry."""
    items: List[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", s)
        if m:
            items.append(m.group(1).strip())
        elif items:
            # continuation of previous bullet
            items[-1] = items[-1] + " " + s
        else:
            items.append(s)
    return items


def emit_body(sections: Dict[str, Any]) -> str:
    parts: List[str] = []
    when = (sections.get("when_to_use") or "").strip()
    if when:
        parts.append(f"## {_KEY_TO_HEADING['when_to_use']}\n\n{when}")
    for key in ("procedure", "pitfalls", "verification"):
        items = sections.get(key) or []
        if not items:
            continue
        heading = _KEY_TO_HEADING[key]
        if key == "procedure":
            body = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(items))
        else:
            body = "\n".join(f"- {x}" for x in items)
        parts.append(f"## {heading}\n\n{body}")
    extra = (sections.get("body_extra") or "").strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) + ("\n" if parts else "")


# ---------------------------------------------------------------------------
# Skill record
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    name: str                                          # slug, dir name
    description: str = ""
    version: str = "1.0.0"
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    platforms: List[str] = field(default_factory=list)
    requires_toolsets: List[str] = field(default_factory=list)
    fallback_for_toolsets: List[str] = field(default_factory=list)
    status: str = "draft"                              # draft | published
    confidence: float = 0.8
    source: str = "learned"
    teacher_model: Optional[str] = None
    owner: Optional[str] = None
    created: str = ""                                  # ISO8601
    when_to_use: str = ""
    procedure: List[str] = field(default_factory=list)
    pitfalls: List[str] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)
    body_extra: str = ""
    # Sidecar (not persisted in SKILL.md)
    uses: int = 0
    last_used: Optional[int] = None
    # File path on disk (set when read)
    path: Optional[str] = None

    # ----------------------------------------------------------------------
    # Serialization
    # ----------------------------------------------------------------------

    def to_frontmatter(self) -> Dict[str, Any]:
        fm: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
        }
        if self.tags:                  fm["tags"] = list(self.tags)
        if self.platforms:             fm["platforms"] = list(self.platforms)
        if self.requires_toolsets:     fm["requires_toolsets"] = list(self.requires_toolsets)
        if self.fallback_for_toolsets: fm["fallback_for_toolsets"] = list(self.fallback_for_toolsets)
        fm["status"] = self.status
        fm["confidence"] = round(float(self.confidence), 3)
        fm["source"] = self.source
        if self.teacher_model: fm["teacher_model"] = self.teacher_model
        if self.owner:         fm["owner"] = self.owner
        fm["created"] = self.created or _now_iso()
        return fm

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.name,        # slug doubles as id
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
            "tags": list(self.tags),
            "platforms": list(self.platforms),
            "requires_toolsets": list(self.requires_toolsets),
            "fallback_for_toolsets": list(self.fallback_for_toolsets),
            "status": self.status,
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "teacher_model": self.teacher_model,
            "owner": self.owner,
            "created": self.created,
            "when_to_use": self.when_to_use,
            "procedure": list(self.procedure),
            "pitfalls": list(self.pitfalls),
            "verification": list(self.verification),
            "body_extra": self.body_extra,
            "uses": int(self.uses or 0),
            "last_used": self.last_used,
            "path": self.path,
        }
        # Back-compat aliases for the old API/UI
        d["title"] = self.description or self.name.replace("-", " ").title()
        d["problem"] = self.when_to_use
        d["solution"] = (self.procedure[0] if self.procedure else "") if not self.body_extra else self.body_extra
        d["steps"] = list(self.procedure)
        return d

    @classmethod
    def from_markdown(cls, text: str, *, path: Optional[str] = None) -> "Skill":
        fm, body = parse_frontmatter(text)
        sections = parse_body(body)
        raw_name = fm.get("name")
        name = slugify(raw_name if raw_name not in (None, "") else fm.get("description", ""), fallback="skill")
        return cls(
            name=name,
            description=str(fm.get("description", "") or ""),
            version=str(fm.get("version", "1.0.0") or "1.0.0"),
            category=str(fm.get("category", "general") or "general"),
            tags=_as_list(fm.get("tags")),
            platforms=_as_list(fm.get("platforms")),
            requires_toolsets=_as_list(fm.get("requires_toolsets")),
            fallback_for_toolsets=_as_list(fm.get("fallback_for_toolsets")),
            status=str(fm.get("status", "draft") or "draft"),
            confidence=_as_float(fm.get("confidence", 0.8), 0.8),
            source=str(fm.get("source", "learned") or "learned"),
            teacher_model=str(fm.get("teacher_model")) if fm.get("teacher_model") else None,
            owner=str(fm.get("owner")) if fm.get("owner") else None,
            created=str(fm.get("created") or _now_iso()),
            when_to_use=sections["when_to_use"],
            procedure=list(sections["procedure"]),
            pitfalls=list(sections["pitfalls"]),
            verification=list(sections["verification"]),
            body_extra=sections["body_extra"],
            path=path,
        )

    def to_markdown(self) -> str:
        fm = emit_frontmatter(self.to_frontmatter())
        body = emit_body({
            "when_to_use": self.when_to_use,
            "procedure": self.procedure,
            "pitfalls": self.pitfalls,
            "verification": self.verification,
            "body_extra": self.body_extra,
        })
        return f"---\n{fm}\n---\n\n{body}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
