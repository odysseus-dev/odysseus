"""Backup routes — export/import user data (memories, presets, settings, skills, preferences)."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.settings import load_settings, save_settings, load_features, save_features

logger = logging.getLogger(__name__)


def _as_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if value in (None, ""):
        return []
    return [str(value)]


def _as_float(value, default=0.8):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_text(value):
    return str(value).strip() if value not in (None, "") else ""


def _owner_key(owner):
    return _as_text(owner)


def _skill_id(skill):
    return _as_text(skill.get("id") or skill.get("name"))


def _skill_label(skill):
    for key in ("title", "description", "name", "id"):
        value = _as_text(skill.get(key))
        if value:
            return value
    return ""


def setup_backup_routes(memory_manager, preset_manager, skills_manager) -> APIRouter:
    router = APIRouter(tags=["backup"])

    @router.get("/api/export")
    async def export_data(request: Request):
        """Export all user data as a downloadable JSON file."""
        require_admin(request)
        user = get_current_user(request)

        # Memories (filtered by owner when auth is enabled)
        memories = memory_manager.load(owner=user)

        # Presets (shared across users — export all)
        presets = preset_manager.get_all()

        # Skills (filtered by owner when auth is enabled)
        skills = skills_manager.load(owner=user)

        # Settings
        settings = load_settings()

        # Feature flags
        features = load_features()

        # User preferences
        from routes.prefs_routes import _load_for_user
        preferences = _load_for_user(user)

        export_data = {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "exported_by": user,
            "memories": memories,
            "presets": presets,
            "skills": skills,
            "settings": settings,
            "features": features,
            "preferences": preferences,
        }

        filename = f"odysseus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=json.dumps(export_data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @router.post("/api/import")
    async def import_data(request: Request):
        """Import user data from a previously exported JSON file. Merges with existing data."""
        require_admin(request)
        user = get_current_user(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object")

        imported = []

        # ── Memories ──
        if "memories" in body and isinstance(body["memories"], list):
            existing = memory_manager.load_all()
            # Dedup against THIS user's own memories only. Using every tenant's
            # rows (load_all) meant a memory whose text matched any other
            # user's was silently skipped, so the importing user lost their own
            # data. The full store is still saved back below.
            existing_texts = {e.get("text", "").strip().lower()
                              for e in existing if e.get("owner") == user}
            added = 0
            for mem in body["memories"]:
                if not isinstance(mem, dict) or not mem.get("text"):
                    continue
                if mem["text"].strip().lower() in existing_texts:
                    continue  # skip duplicates
                # Assign owner when auth is enabled
                if user and not mem.get("owner"):
                    mem["owner"] = user
                existing.append(mem)
                existing_texts.add(mem["text"].strip().lower())
                added += 1
            memory_manager.save(existing)
            imported.append(f"{added} memories")

        # ── Skills ──
        if "skills" in body and isinstance(body["skills"], list):
            existing = skills_manager.load_all()
            existing_ids = {
                (_owner_key(s.get("owner")), _skill_id(s))
                for s in existing
                if _skill_id(s)
            }
            existing_titles = {
                (_owner_key(s.get("owner")), _skill_label(s).lower())
                for s in existing
                if _skill_label(s)
            }
            added = 0
            for skill in body["skills"]:
                if not isinstance(skill, dict):
                    continue
                skill = dict(skill)
                label = _skill_label(skill)
                if not label:
                    continue
                target_owner = _as_text(skill.get("owner")) or user
                owner_key = _owner_key(target_owner)
                skill_id = _skill_id(skill)
                title_key = label.lower()
                # Skip if the importing owner's library already has this skill.
                if skill_id and (owner_key, skill_id) in existing_ids:
                    continue
                if (owner_key, title_key) in existing_titles:
                    continue
                if user and not _as_text(skill.get("owner")):
                    skill["owner"] = user
                    target_owner = user
                    owner_key = _owner_key(target_owner)

                body_extra = skill.get("body_extra")
                created = _as_text(skill.get("created")) or None
                result = skills_manager.add_skill(
                    title=_as_text(skill.get("title") or skill.get("description") or skill.get("name")),
                    problem=_as_text(skill.get("problem") if skill.get("problem") is not None else skill.get("when_to_use")),
                    solution=_as_text(skill.get("solution") if skill.get("solution") is not None else skill.get("body_extra")),
                    steps=_as_list(skill.get("steps") if skill.get("steps") is not None else skill.get("procedure")),
                    tags=_as_list(skill.get("tags")),
                    source=_as_text(skill.get("source")) or "imported",
                    teacher_model=skill.get("teacher_model"),
                    confidence=_as_float(skill.get("confidence"), 0.8),
                    owner=target_owner or None,
                    name=_as_text(skill.get("name") or skill.get("id")) or None,
                    description=_as_text(skill.get("description") or skill.get("title")),
                    category=_as_text(skill.get("category")) or "general",
                    when_to_use=_as_text(skill.get("when_to_use") if skill.get("when_to_use") is not None else skill.get("problem")),
                    procedure=_as_list(skill.get("procedure") if skill.get("procedure") is not None else skill.get("steps")),
                    pitfalls=_as_list(skill.get("pitfalls")),
                    verification=_as_list(skill.get("verification")),
                    platforms=_as_list(skill.get("platforms")),
                    requires_toolsets=_as_list(skill.get("requires_toolsets")),
                    fallback_for_toolsets=_as_list(skill.get("fallback_for_toolsets")),
                    status=_as_text(skill.get("status")) or "draft",
                    version=_as_text(skill.get("version")) or "1.0.0",
                    body_extra=str(body_extra) if body_extra not in (None, "") else None,
                    created=created,
                )
                if isinstance(result, dict):
                    stored_owner_key = _owner_key(result.get("owner"))
                    stored_id = _skill_id(result)
                    stored_label = _skill_label(result)
                    if stored_id:
                        existing_ids.add((stored_owner_key, stored_id))
                    if stored_label:
                        existing_titles.add((stored_owner_key, stored_label.lower()))
                    if not result.get("_deduped"):
                        added += 1
                else:
                    added += 1
                if skill_id:
                    existing_ids.add((owner_key, skill_id))
                existing_titles.add((owner_key, title_key))
            imported.append(f"{added} skills")

        # ── Presets ──
        if "presets" in body and isinstance(body["presets"], dict):
            current = preset_manager.get_all()
            for key, value in body["presets"].items():
                if isinstance(value, dict):
                    current[key] = value
                elif isinstance(value, list):
                    current[key] = value
            preset_manager.save(current)
            imported.append("presets")

        # ── Settings ──
        if "settings" in body and isinstance(body["settings"], dict):
            current = load_settings()
            current.update(body["settings"])
            save_settings(current)
            imported.append("settings")

        # ── Features ──
        if "features" in body and isinstance(body["features"], dict):
            current = load_features()
            current.update(body["features"])
            save_features(current)
            imported.append("features")

        # ── Preferences ──
        if "preferences" in body and isinstance(body["preferences"], dict):
            from routes.prefs_routes import _load_for_user, _save_for_user
            current = _load_for_user(user)
            current.update(body["preferences"])
            _save_for_user(user, current)
            imported.append("preferences")

        if not imported:
            return {"ok": False, "message": "No recognized data found in the file"}

        return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}

    return router
