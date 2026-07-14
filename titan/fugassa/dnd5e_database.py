"""D&D 5e SRD cache — port of Fugassa-II `DnD5eDatabase.gd`."""

from __future__ import annotations

import json
import math
import os
from typing import Any

from titan.fugassa.paths import DND5E_DIR

_FILES: dict[str, str] = {
    "classes": "classes.json",
    "subclasses": "subclasses.json",
    "races": "races.json",
    "skills": "skills.json",
    "features": "features.json",
    "traits": "traits.json",
    "feats": "feats.json",
    "ability_scores": "ability_scores.json",
    "spells": "spells.json",
    "index": "index.json",
}


class Dnd5eDatabase:
    def __init__(self, data_dir: str | None = None) -> None:
        self._data_dir = data_dir or DND5E_DIR
        self._loaded = False
        self._classes: list[dict[str, Any]] = []
        self._subclasses_by_class: dict[str, list[dict[str, Any]]] = {}
        self._races: list[dict[str, Any]] = []
        self._skills: list[dict[str, Any]] = []
        self._features: list[dict[str, Any]] = []
        self._traits: list[dict[str, Any]] = []
        self._feats: list[dict[str, Any]] = []
        self._ability_scores: list[dict[str, Any]] = []
        self._spells: list[dict[str, Any]] = []
        self._manifest: dict[str, Any] = {}
        self._classes_by_index: dict[str, dict[str, Any]] = {}
        self._races_by_index: dict[str, dict[str, Any]] = {}
        self._skills_by_index: dict[str, dict[str, Any]] = {}
        self._spells_by_index: dict[str, dict[str, Any]] = {}
        self._features_by_index: dict[str, dict[str, Any]] = {}
        self._traits_by_index: dict[str, dict[str, Any]] = {}
        self._spells_by_class_level: dict[str, list[dict[str, Any]]] = {}

    def load_all(self) -> bool:
        if self._loaded:
            return True
        self._classes = self._read_json("classes", [])
        self._subclasses_by_class = self._read_json("subclasses", {})
        self._races = self._read_json("races", [])
        self._skills = self._read_json("skills", [])
        self._features = self._read_json("features", [])
        self._traits = self._read_json("traits", [])
        self._feats = self._read_json("feats", [])
        self._ability_scores = self._read_json("ability_scores", [])
        self._spells = self._read_json("spells", [])
        self._manifest = self._read_json("index", {})
        self._rebuild_indexes()
        self._loaded = bool(self._classes and self._races)
        return self._loaded

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def manifest(self) -> dict[str, Any]:
        return dict(self._manifest)

    def reload(self) -> bool:
        self._loaded = False
        self._classes.clear()
        self._subclasses_by_class.clear()
        self._races.clear()
        self._skills.clear()
        self._features.clear()
        self._traits.clear()
        self._feats.clear()
        self._ability_scores.clear()
        self._spells.clear()
        self._manifest.clear()
        self._classes_by_index.clear()
        self._races_by_index.clear()
        self._skills_by_index.clear()
        self._spells_by_index.clear()
        self._features_by_index.clear()
        self._traits_by_index.clear()
        self._spells_by_class_level.clear()
        return self.load_all()

    def list_classes(self) -> list[dict[str, Any]]:
        return self._classes

    def list_races(self) -> list[dict[str, Any]]:
        return self._races

    def list_skills(self) -> list[dict[str, Any]]:
        return self._skills

    def list_feats(self) -> list[dict[str, Any]]:
        return self._feats

    def list_ability_scores(self) -> list[dict[str, Any]]:
        return self._ability_scores

    def list_all_spells(self) -> list[dict[str, Any]]:
        return self._spells

    def get_class_data(self, class_index: str) -> dict[str, Any]:
        return dict(self._classes_by_index.get(str(class_index or "").lower(), {}))

    def list_subclasses_for(self, class_index: str) -> list[dict[str, Any]]:
        return list(self._subclasses_by_class.get(str(class_index or "").lower(), []))

    def get_subclass(self, class_index: str, sub_index: str) -> dict[str, Any]:
        sid = str(sub_index or "").lower()
        for sub in self.list_subclasses_for(class_index):
            if str(sub.get("index", "")) == sid:
                return dict(sub)
        return {}

    def get_class_level(self, class_index: str, level: int) -> dict[str, Any]:
        cls = self.get_class_data(class_index)
        if not cls:
            return {}
        for row in cls.get("levels") or []:
            if isinstance(row, dict) and int(row.get("level", 0)) == int(level):
                return dict(row)
        return {}

    def list_class_features_up_to(self, class_index: str, level: int) -> list[Any]:
        out: list[Any] = []
        for lvl in range(1, max(1, min(int(level), 20)) + 1):
            row = self.get_class_level(class_index, lvl)
            if not row:
                continue
            for feat in row.get("features") or []:
                out.append(feat)
        return out

    def list_subclass_features_up_to(self, class_index: str, sub_index: str, level: int) -> list[Any]:
        sub = self.get_subclass(class_index, sub_index)
        if not sub:
            return []
        out: list[Any] = []
        for row in sub.get("levels") or []:
            if not isinstance(row, dict):
                continue
            lvl = int(row.get("level", 0))
            if lvl > int(level):
                continue
            for feat in row.get("features") or []:
                out.append(feat)
        return out

    @staticmethod
    def proficiency_bonus_at_level(level: int) -> int:
        lvl = max(1, min(int(level), 20))
        return 2 + (lvl - 1) // 4

    def get_race(self, race_index: str) -> dict[str, Any]:
        return dict(self._races_by_index.get(str(race_index or "").lower(), {}))

    def get_subrace(self, race_index: str, sub_index: str) -> dict[str, Any]:
        race = self.get_race(race_index)
        if not race:
            return {}
        sid = str(sub_index or "").lower()
        for sub in race.get("subraces_detail") or []:
            if isinstance(sub, dict) and str(sub.get("index", "")) == sid:
                return dict(sub)
        return {}

    def ability_bonuses_for(self, race_index: str, sub_index: str = "") -> dict[str, int]:
        out: dict[str, int] = {}
        race = self.get_race(race_index)
        if not race:
            return out
        for bonus in race.get("ability_bonuses") or []:
            if not isinstance(bonus, dict):
                continue
            ab = bonus.get("ability_score") or {}
            key = str(ab.get("index", "")).lower()
            val = int(bonus.get("bonus", 0))
            if key:
                out[key] = out.get(key, 0) + val
        if sub_index:
            sub = self.get_subrace(race_index, sub_index)
            for bonus in sub.get("ability_bonuses") or []:
                if not isinstance(bonus, dict):
                    continue
                ab = bonus.get("ability_score") or {}
                key = str(ab.get("index", "")).lower()
                val = int(bonus.get("bonus", 0))
                if key:
                    out[key] = out.get(key, 0) + val
        return out

    def list_traits_for(self, race_index: str, sub_index: str = "") -> list[Any]:
        out: list[Any] = []
        race = self.get_race(race_index)
        for trait in race.get("traits") or []:
            out.append(trait)
        if sub_index:
            sub = self.get_subrace(race_index, sub_index)
            for trait in sub.get("racial_traits") or []:
                out.append(trait)
        return out

    def get_skill(self, skill_index: str) -> dict[str, Any]:
        return dict(self._skills_by_index.get(str(skill_index or "").lower(), {}))

    def get_feature(self, feature_index: str) -> dict[str, Any]:
        return dict(self._features_by_index.get(str(feature_index or "").lower(), {}))

    def get_trait(self, trait_index: str) -> dict[str, Any]:
        return dict(self._traits_by_index.get(str(trait_index or "").lower(), {}))

    def get_spell(self, spell_index: str) -> dict[str, Any]:
        return dict(self._spells_by_index.get(str(spell_index or "").lower(), {}))

    def list_spells_for(self, class_index: str, spell_level: int = -1) -> list[dict[str, Any]]:
        key = f"{str(class_index or '').lower()}_{int(spell_level)}"
        if key in self._spells_by_class_level:
            return self._spells_by_class_level[key]
        out: list[dict[str, Any]] = []
        target_class = str(class_index or "").lower()
        for spell in self._spells:
            if not isinstance(spell, dict):
                continue
            if spell_level >= 0 and int(spell.get("level", -1)) != spell_level:
                continue
            for cls_ref in spell.get("classes") or []:
                if isinstance(cls_ref, dict) and str(cls_ref.get("index", "")).lower() == target_class:
                    out.append(spell)
                    break
        self._spells_by_class_level[key] = out
        return out

    @staticmethod
    def point_buy_cost(score: int) -> int:
        table = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
        return table.get(int(score), -1)

    @staticmethod
    def point_buy_budget() -> int:
        return 27

    @staticmethod
    def point_buy_min() -> int:
        return 8

    @staticmethod
    def point_buy_max() -> int:
        return 15

    @staticmethod
    def point_buy_total_spent(abilities_pre_race: dict[str, Any]) -> int:
        total = 0
        for key in ("str", "dex", "con", "int", "wis", "cha"):
            cost = Dnd5eDatabase.point_buy_cost(int(abilities_pre_race.get(key, 8)))
            if cost < 0:
                return -1
            total += cost
        return total

    def _read_json(self, key: str, fallback: Any) -> Any:
        rel = _FILES.get(key, "")
        if not rel:
            return fallback
        path = os.path.join(self._data_dir, rel)
        if not os.path.isfile(path):
            return fallback
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _rebuild_indexes(self) -> None:
        self._classes_by_index = {
            str(row.get("index", "")).lower(): row
            for row in self._classes
            if isinstance(row, dict) and row.get("index")
        }
        self._races_by_index = {
            str(row.get("index", "")).lower(): row
            for row in self._races
            if isinstance(row, dict) and row.get("index")
        }
        self._skills_by_index = {
            str(row.get("index", "")).lower(): row
            for row in self._skills
            if isinstance(row, dict) and row.get("index")
        }
        self._spells_by_index = {
            str(row.get("index", "")).lower(): row
            for row in self._spells
            if isinstance(row, dict) and row.get("index")
        }
        self._features_by_index = {
            str(row.get("index", "")).lower(): row
            for row in self._features
            if isinstance(row, dict) and row.get("index")
        }
        self._traits_by_index = {
            str(row.get("index", "")).lower(): row
            for row in self._traits
            if isinstance(row, dict) and row.get("index")
        }


_db: Dnd5eDatabase | None = None


def get_dnd5e_database() -> Dnd5eDatabase:
    global _db
    if _db is None:
        _db = Dnd5eDatabase()
        _db.load_all()
    return _db
