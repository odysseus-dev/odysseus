"""Archivist property op validation — rejects vague or incomplete patches."""

from __future__ import annotations

from typing import Any

VALID_PROPERTY_KINDS = frozenset(
    {"townhouse", "cottage", "estate", "apartment", "shop", "warehouse", "castle", "land"}
)
VALID_TITLE_STATUS = frozenset({"owned", "leased", "pending", "disputed", "contested", "managed", "planned"})
VALID_FIXTURE_KINDS = frozenset(
    {"furniture", "storage", "decoration", "crafting_station", "lighting", "security", "appliance"}
)
VALID_STAFF_ROLES = frozenset(
    {"steward", "guard", "maid", "cook", "concubine", "servant", "staff", "butler", "housekeeper"}
)


def validate_archivist_property_op(op: dict[str, Any]) -> str | None:
    """Return error message when invalid, else None."""
    name = str(op.get("name") or "").strip()
    if len(name) < 3:
        return "property name required"
    kind = str(op.get("property_kind") or "townhouse").strip().lower()
    if kind not in VALID_PROPERTY_KINDS:
        return f"invalid property_kind: {kind}"
    status = str(op.get("title_status") or "owned").strip().lower()
    if status not in VALID_TITLE_STATUS:
        return f"invalid title_status: {status}"
    deed = str(op.get("deed_summary") or "").strip()
    if len(deed) < 8:
        return "deed_summary required for new property"
    specs = op.get("specs")
    if specs is not None and not isinstance(specs, dict):
        return "specs must be an object"
    return None


def validate_archivist_property_update_op(op: dict[str, Any]) -> str | None:
    code = str(op.get("code") or op.get("property_code") or "").strip()
    name = str(op.get("name") or op.get("property_name") or "").strip()
    if not code and not name:
        return "property code or name required for update"
    if op.get("deed_append") and not str(op.get("deed_append") or "").strip():
        return "deed_append must be non-empty when present"
    if op.get("property_kind"):
        kind = str(op.get("property_kind") or "").strip().lower()
        if kind not in VALID_PROPERTY_KINDS:
            return f"invalid property_kind: {kind}"
    if op.get("title_status"):
        status = str(op.get("title_status") or "").strip().lower()
        if status not in VALID_TITLE_STATUS:
            return f"invalid title_status: {status}"
    if op.get("specs") is not None and not isinstance(op.get("specs"), dict):
        return "specs must be an object"
    return None


def validate_archivist_property_room_op(op: dict[str, Any]) -> str | None:
    prop_ref = str(op.get("property_name") or op.get("property_code") or "").strip()
    room_name = str(op.get("room_name") or op.get("name") or "").strip()
    if not prop_ref:
        return "property_name or property_code required for property_room"
    if len(room_name) < 2:
        return "room_name required"
    return None


def validate_archivist_property_fixture_op(op: dict[str, Any]) -> str | None:
    prop_ref = str(op.get("property_name") or op.get("property_code") or "").strip()
    name = str(op.get("name") or "").strip()
    if not prop_ref:
        return "property_name or property_code required for property_fixture"
    if len(name) < 2:
        return "fixture name required"
    kind = str(op.get("fixture_kind") or "furniture").strip().lower()
    if kind not in VALID_FIXTURE_KINDS:
        return f"invalid fixture_kind: {kind}"
    if op.get("specs") is not None and not isinstance(op.get("specs"), dict):
        return "specs must be an object"
    return None


def validate_archivist_property_staff_op(op: dict[str, Any]) -> str | None:
    prop_ref = str(op.get("property_name") or op.get("property_code") or "").strip()
    npc_ref = str(op.get("npc_name") or op.get("npc_code") or "").strip()
    if not prop_ref:
        return "property_name or property_code required for property_staff"
    if not npc_ref:
        return "npc_name or npc_code required for property_staff"
    role = str(op.get("role") or "staff").strip().lower()
    if role not in VALID_STAFF_ROLES:
        return f"invalid staff role: {role}"
    return None
