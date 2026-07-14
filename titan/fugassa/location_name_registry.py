"""Per-save place-name registry — avoid duplicate city/village/landmark names."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

REGISTRY_META_KEY = "place_name_registry_json"
_GENERIC_SETTLEMENT_PREFIXES = frozenset({"city", "town", "village", "settlement", "the city", "the town"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


@dataclass
class PlaceNameRegistry:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def names(self) -> set[str]:
        return {_norm(e.get("name")) for e in self.entries if e.get("name")}

    def by_kind(self, kind: str) -> list[str]:
        k = _norm(kind)
        return [str(e.get("name") or "") for e in self.entries if _norm(e.get("kind")) == k and e.get("name")]


def load_registry(db_path: str) -> PlaceNameRegistry:
    if not db_path:
        return PlaceNameRegistry()
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM save_meta WHERE key = ?",
            (REGISTRY_META_KEY,),
        ).fetchone()
        if not row or not row[0]:
            return PlaceNameRegistry()
        data = json.loads(row[0])
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return PlaceNameRegistry()
        return PlaceNameRegistry(entries=[e for e in entries if isinstance(e, dict)])
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        return PlaceNameRegistry()
    finally:
        conn.close()


def save_registry(db_path: str, registry: PlaceNameRegistry) -> None:
    conn = sqlite3.connect(db_path)
    try:
        payload = json.dumps({"entries": registry.entries}, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO save_meta (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (REGISTRY_META_KEY, payload, _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def seed_registry_from_locations(db_path: str, *, persist: bool = True) -> PlaceNameRegistry:
    """Import grid-level settlement names already stored on locations."""
    registry = load_registry(db_path)
    known = registry.names()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, name, region_name, parent_location_id
            FROM locations
            WHERE parent_location_id IS NULL
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            settlement = str(row["region_name"] or "").strip()
            if not settlement:
                settlement = settlement_from_location_name(str(row["name"] or ""))
            if not settlement or _is_generic_settlement(settlement):
                continue
            norm = _norm(settlement)
            if norm in known:
                continue
            registry.entries.append(
                {
                    "name": settlement,
                    "kind": "settlement",
                    "entity_type": "location",
                    "entity_id": int(row["id"]),
                }
            )
            known.add(norm)
    finally:
        conn.close()
    if persist:
        save_registry(db_path, registry)
    return registry


def _is_generic_settlement(name: str) -> bool:
    return _norm(name) in _GENERIC_SETTLEMENT_PREFIXES or _norm(name).startswith("city ")


def settlement_from_location_name(name: str) -> str:
    """Extract settlement prefix from 'Crownstone — Market District' style names."""
    text = str(name or "").strip()
    for sep in (" — ", " – ", " - "):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if head and not _is_generic_settlement(head):
                return head
    if text and not _is_generic_settlement(text):
        return text
    return ""


def district_from_location_name(name: str) -> str:
    text = str(name or "").strip()
    for sep in (" — ", " – ", " - "):
        if sep in text:
            tail = text.split(sep, 1)[1].strip()
            if tail:
                return tail
    return text


def name_collision(registry: PlaceNameRegistry, name: str) -> bool:
    norm = _norm(name)
    return bool(norm) and norm in registry.names()


def reserve_name(
    registry: PlaceNameRegistry,
    *,
    name: str,
    kind: str = "settlement",
    entity_type: str = "location",
    entity_id: int | None = None,
) -> str:
    final = str(name or "").strip()
    if not final:
        return final
    norm = _norm(final)
    registry.entries = [e for e in registry.entries if _norm(e.get("name") or "") != norm]
    registry.entries.append(
        {
            "name": final,
            "kind": kind,
            "entity_type": entity_type,
            "entity_id": entity_id,
        }
    )
    return final


def register_settlement(
    db_path: str,
    *,
    name: str,
    location_id: int | None = None,
    kind: str = "city",
) -> str:
    registry = seed_registry_from_locations(db_path, persist=False)
    final = str(name or "").strip()
    if not final:
        return final
    if name_collision(registry, final):
        return final
    reserve_name(registry, name=final, kind=kind, entity_type="location", entity_id=location_id)
    save_registry(db_path, registry)
    if db_path and location_id:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE locations SET region_name = ?, updated_at = ? WHERE id = ?",
                (final, _utc_now(), int(location_id)),
            )
            conn.commit()
        finally:
            conn.close()
    return final


def prompt_block(registry: PlaceNameRegistry, *, limit: int = 24) -> str:
    names = [str(e.get("name") or "").strip() for e in registry.entries if e.get("name")]
    if not names:
        return ""
    shown = names[:limit]
    more = len(names) - len(shown)
    tail = f" (+{more} more)" if more > 0 else ""
    return (
        "NAMED PLACES (mandatory — every city, village, or significant settlement must have "
        f"a distinct proper name; never reuse: {', '.join(shown)}{tail}. "
        "Do not call settlements generic 'City' or 'Town' without a proper name."
    )


def resolve_settlement_labels(
    *,
    name: str,
    region_name: str | None = None,
    parent_location_id: int | None = None,
    parent_region_name: str | None = None,
    parent_name: str | None = None,
) -> dict[str, str]:
    """Build HUD/GM settlement labels for a location row."""
    settlement = str(region_name or "").strip()
    if not settlement and parent_region_name:
        settlement = str(parent_region_name).strip()
    if not settlement and not parent_location_id:
        settlement = settlement_from_location_name(name)
    district = district_from_location_name(name) if settlement else str(name or "").strip()
    if parent_location_id and settlement:
        district = str(name or district).strip()
    place_label = ""
    if settlement and district and _norm(settlement) != _norm(district):
        place_label = f"{settlement} · {district}"
    elif settlement:
        place_label = settlement
    else:
        place_label = district or str(name or "Unknown")
    return {
        "settlement_name": settlement,
        "district_name": district,
        "place_label": place_label,
    }
