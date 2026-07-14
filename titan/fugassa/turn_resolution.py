"""Turn resolution model — ADR §G / §G1 JSON kanon."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml


@dataclass
class TurnResolution:
    mode: str = "action"  # action | narrative_only | engine_only
    intent: str = "narrative_only"
    binding_summary: str = ""
    time_delta_minutes: int = 0
    # ADR §8d: "turn_resolution vždy actor: player_character_id" — party is
    # 1+4 capable in schema, but M1-M5 gameplay stays solo, so this is always
    # the hero's `player_characters.id` until multi-character control ships.
    actor: int | None = None
    travel: dict[str, Any] = field(default_factory=dict)
    combat: dict[str, Any] = field(default_factory=dict)
    social: dict[str, Any] = field(default_factory=dict)
    quest: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, Any] = field(default_factory=dict)
    currency: list[dict[str, Any]] = field(default_factory=list)
    sheet_delta: dict[str, Any] = field(default_factory=dict)
    exploration: dict[str, Any] = field(default_factory=dict)
    search: dict[str, Any] = field(default_factory=dict)
    asset_requests: list[dict[str, Any]] = field(default_factory=list)
    guard: dict[str, Any] = field(default_factory=dict)
    gm_instruction: str = ""
    agenda: dict[str, Any] = field(default_factory=dict)
    secret_gm_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TurnResolution:
        if not data:
            return cls()
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: data[k] for k in data if k in fields}
        return cls(**kwargs)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_prompt_yaml(self) -> str:
        payload = self.to_dict()
        # secret_gm_notes must never land in the "binding — narrate faithfully"
        # block; it is GM-only facade info surfaced separately (see
        # context_builder), never mechanical truth the player has learned.
        payload.pop("secret_gm_notes", None)
        if not payload.get("binding_summary"):
            payload["binding_summary"] = self._auto_binding_summary()
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)

    def _auto_binding_summary(self) -> str:
        parts: list[str] = []
        if self.travel:
            parts.append(f"travel: {self.travel.get('summary', self.travel)}")
        if self.combat:
            parts.append(f"combat: {self.combat.get('summary', 'active')}")
        if self.search:
            parts.append(f"search: {self.search.get('summary', self.search)}")
        if self.inventory:
            parts.append(f"inventory: {self.inventory.get('summary', 'changed')}")
        if self.currency:
            from titan.fugassa.currency_engine import resolution_currency_summary

            cur = resolution_currency_summary(self.currency)
            if cur:
                parts.append(f"currency: {cur}")
        if self.quest:
            parts.append(f"quest: {self.quest.get('summary', self.quest)}")
        if self.exploration:
            parts.append(f"exploration: {self.exploration.get('summary', self.exploration)}")
        return "; ".join(parts) if parts else "no mechanical changes"

    def requires_gm(self) -> bool:
        return self.mode != "engine_only"

    def requires_archivist(self) -> bool:
        return self.mode in ("action", "narrative_only")
