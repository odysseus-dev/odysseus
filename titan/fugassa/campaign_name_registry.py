"""Per-save NPC name registry — avoid duplicate first/last names and full-name collisions."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

REGISTRY_META_KEY = "name_registry_json"
_MAX_LAST_NAME_REUSE = 1  # allow one NPC per last name; second reuse is blocked

_SUFFIX_POOL = (
    "Ashford",
    "Vale",
    "Thorne",
    "Solari",
    "Merrick",
    "Lysande",
    "Corvin",
    "Halden",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def split_person_name(full_name: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(full_name or "").strip())
    if not text:
        return "", ""
    parts = text.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _norm(token: str) -> str:
    return re.sub(r"\s+", " ", str(token or "").strip().lower())


@dataclass
class CampaignNameRegistry:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def first_names(self) -> set[str]:
        return {_norm(e.get("first_name")) for e in self.entries if e.get("first_name")}

    def last_names(self) -> set[str]:
        return {_norm(e.get("last_name")) for e in self.entries if e.get("last_name")}

    def full_names(self) -> set[str]:
        return {_norm(e.get("full_name")) for e in self.entries if e.get("full_name")}

    def pairs(self) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for e in self.entries:
            first = _norm(e.get("first_name"))
            last = _norm(e.get("last_name"))
            if first and last:
                out.add((first, last))
        return out

    def last_name_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entries:
            last = _norm(e.get("last_name"))
            if last:
                counts[last] = counts.get(last, 0) + 1
        return counts


def load_registry(db_path: str) -> CampaignNameRegistry:
    if not db_path:
        return CampaignNameRegistry()
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM save_meta WHERE key = ?",
            (REGISTRY_META_KEY,),
        ).fetchone()
        if not row or not row[0]:
            return CampaignNameRegistry()
        data = json.loads(row[0])
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return CampaignNameRegistry()
        return CampaignNameRegistry(entries=[e for e in entries if isinstance(e, dict)])
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        return CampaignNameRegistry()
    finally:
        conn.close()


def save_registry(db_path: str, registry: CampaignNameRegistry) -> None:
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


def seed_registry_from_npcs(db_path: str, *, persist: bool = True) -> CampaignNameRegistry:
    """Import existing `npcs` rows into the registry (idempotent by entity_id)."""
    registry = load_registry(db_path)
    known_ids = {int(e["entity_id"]) for e in registry.entries if e.get("entity_type") == "npc" and e.get("entity_id")}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute("SELECT id, name FROM npcs ORDER BY id ASC"):
            npc_id = int(row["id"])
            if npc_id in known_ids:
                continue
            full_name = str(row["name"] or "").strip()
            if not full_name:
                continue
            first, last = split_person_name(full_name)
            registry.entries.append(
                {
                    "full_name": full_name,
                    "first_name": first,
                    "last_name": last,
                    "entity_type": "npc",
                    "entity_id": npc_id,
                }
            )
    finally:
        conn.close()
    if persist:
        save_registry(db_path, registry)
    return registry


def prompt_block(registry: CampaignNameRegistry, *, limit: int = 40) -> str:
    names = [str(e.get("full_name") or "").strip() for e in registry.entries if e.get("full_name")]
    if not names:
        return ""
    shown = names[:limit]
    more = len(names) - len(shown)
    tail = f" (+{more} more)" if more > 0 else ""
    return (
        "NAME REGISTRY (mandatory — do not reuse any listed first or last name, "
        f"or full-name combination): {', '.join(shown)}{tail}"
    )


def name_collision(registry: CampaignNameRegistry, full_name: str) -> bool:
    full = str(full_name or "").strip()
    if not full:
        return True
    norm_full = _norm(full)
    if norm_full in registry.full_names():
        return True
    first, last = split_person_name(full)
    nf, nl = _norm(first), _norm(last)
    if nf and nf in registry.first_names():
        return True
    if nl:
        if nl in registry.last_names() and registry.last_name_counts().get(nl, 0) >= _MAX_LAST_NAME_REUSE:
            return True
        if nf and (nf, nl) in registry.pairs():
            return True
    return False


def uniquify_name(
    registry: CampaignNameRegistry,
    proposed: str,
    *,
    role: str | None = None,
) -> str:
    base = str(proposed or "").strip()
    if base and not name_collision(registry, base):
        return base
    first, last = split_person_name(base)
    role_word = re.sub(r"[^a-zA-Z]+", " ", str(role or "")).strip().split(" ")
    role_word = role_word[0] if role_word else ""
    candidates: list[str] = []
    if role_word and first:
        candidates.append(f"{role_word} {first}")
    if role_word and not first:
        candidates.append(role_word)
    for suffix in _SUFFIX_POOL:
        if suffix.lower() == last.lower():
            continue
        if first:
            candidates.append(f"{first} {suffix}")
        elif base:
            candidates.append(f"{base} {suffix}")
    for n in range(2, 12):
        candidates.append(f"{first or base} {last or 'Walker'} {n}".strip())
    for cand in candidates:
        cand = re.sub(r"\s+", " ", cand).strip()
        if len(cand) >= 2 and not name_collision(registry, cand):
            return cand[:80]
    return base[:80]


def reserve_name(
    registry: CampaignNameRegistry,
    *,
    full_name: str,
    entity_type: str,
    entity_id: int,
) -> str:
    final = str(full_name or "").strip()
    if not final:
        return final
    if name_collision(registry, final):
        final = uniquify_name(registry, final)
    first, last = split_person_name(final)
    registry.entries.append(
        {
            "full_name": final,
            "first_name": first,
            "last_name": last,
            "entity_type": entity_type,
            "entity_id": int(entity_id),
        }
    )
    return final


def prepare_npc_name(
    db_path: str,
    proposed: str,
    *,
    role: str | None = None,
) -> str:
    """Return a collision-free name without reserving (reserve after SQL insert)."""
    registry = seed_registry_from_npcs(db_path, persist=False)
    return uniquify_name(registry, proposed, role=role)


def sanitize_population_plan(plan: dict[str, Any], db_path: str) -> dict[str, Any]:
    """Rename population NPC entries that collide with the campaign registry."""
    registry = seed_registry_from_npcs(db_path, persist=False)
    out = dict(plan)
    renamed = 0
    for key in ("present_npcs", "hidden_npcs"):
        entries = []
        for raw in plan.get(key) or []:
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            role = str(entry.get("role") or "").strip() or None
            if name_collision(registry, name):
                entry["name"] = uniquify_name(registry, name, role=role)
                renamed += 1
            entries.append(entry)
        out[key] = entries
    out["name_registry_renames"] = renamed
    return out


def register_spawned_npc(db_path: str, *, npc_id: int, name: str) -> str:
    """Record a spawned NPC name after insert (updates registry if name changed)."""
    registry = seed_registry_from_npcs(db_path, persist=False)
    final = str(name or "").strip()
    # Replace placeholder row for this id if present
    registry.entries = [
        e for e in registry.entries
        if not (e.get("entity_type") == "npc" and int(e.get("entity_id") or 0) == int(npc_id))
    ]
    reserve_name(registry, full_name=final, entity_type="npc", entity_id=int(npc_id))
    save_registry(db_path, registry)
    return final
