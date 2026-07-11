"""Compact, read-only capability context for Misumi personas."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.seed_order_context import _resolve_seed_root

try:
    import yaml
except ImportError:  # PyYAML is optional at runtime; capability context degrades safely.
    yaml = None


logger = logging.getLogger(__name__)

_CAPABILITY_FILE = Path("config/personas.yaml")
_PANELS = {
    "sanji": ("food", "PANTRY"),
    "jin": ("records", "RECORDS"),
    "ginko": ("plants", "GARDEN"),
    "misato": ("cleaning", "ROTA"),
}
_CACHE: dict[Path, tuple[tuple[int, int], Mapping[str, Any] | None]] = {}


def _load_personas(path: Path) -> Mapping[str, Any] | None:
    """Load and mtime-cache the persona mapping without propagating failures."""
    if yaml is None:
        return None
    try:
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = _CACHE.get(path)
        if cached and cached[0] == signature:
            return cached[1]

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            personas = None
        else:
            candidate = raw.get("personas", raw)
            personas = candidate if isinstance(candidate, Mapping) else None
        _CACHE[path] = (signature, personas)
        return personas
    except Exception as exc:
        logger.debug("Misumi persona capabilities unavailable: %s", exc, exc_info=True)
        return None


def _items(value: Any) -> list[str] | None:
    """Normalize a scalar, sequence, or intent mapping to compact text items."""
    if value is None:
        return []
    if isinstance(value, str):
        item = " ".join(value.split())
        return [item] if item else []
    if isinstance(value, Mapping):
        values = value.keys()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return None

    items: list[str] = []
    for value_item in values:
        if not isinstance(value_item, (str, int, float)) or isinstance(value_item, bool):
            return None
        item = " ".join(str(value_item).split())
        if item:
            items.append(item)
    return items


def _line(label: str, value: Any) -> str | None:
    items = _items(value)
    if items is None:
        return None
    return f"{label}: {', '.join(items) if items else 'none specified'}"


def capability_summary(persona_id: object) -> str | None:
    """Return a bounded persona capability block, or ``None`` if unavailable."""
    try:
        root = _resolve_seed_root()
        if root is None:
            return None
        path = (root / _CAPABILITY_FILE).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None

        personas = _load_personas(path)
        persona = str(persona_id or "").strip().lower()
        record = personas.get(persona) if personas else None
        if not isinstance(record, Mapping):
            return None

        role = record.get("role")
        if not isinstance(role, str) or not role.strip():
            return None
        routing = record.get("routing")
        if routing is None:
            routing = {}
        if not isinstance(routing, Mapping):
            return None

        field_lines = (
            _line("Skills", record.get("skills")),
            _line("Stewarded domains", record.get("owns")),
            _line("Consults", record.get("consults")),
            _line("Escalates to", record.get("escalates_to")),
            _line("Routing intents", routing.get("intents")),
        )
        if any(line is None for line in field_lines):
            return None

        lines = [
            f"Capabilities for {persona}:",
            f"Role: {' '.join(role.split())}",
            *(line for line in field_lines if line is not None),
        ]
        if persona in _PANELS:
            domain, panel = _PANELS[persona]
            lines.append(
                f"Interface panel: {domain}/{panel}; household writes are proposals for user ratification."
            )
        if persona == "aoteru":
            lines.append(
                "Head-interfacer routing: triage requests and route or consult the capable persona."
            )
        lines.append(
            "Passive memory: Misumi keeps a local passive memory for capture, open-loops, and glance; it never writes to the household."
        )
        return "\n".join(lines[:13])
    except Exception as exc:
        logger.debug("Misumi persona capability summary unavailable: %s", exc, exc_info=True)
        return None
