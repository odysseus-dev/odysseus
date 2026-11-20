"""Backup routes — export/import user data (memories, presets, settings, skills, preferences)."""

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from core.middleware import require_admin
from services.memory import MemoryStoreUnreadable
from src.auth_helpers import get_current_user
from src.settings import load_settings, save_settings, load_features, save_features

logger = logging.getLogger(__name__)


def _merge_by_id(current, incoming: list) -> list:
    """Merge a backed-up list (user templates, group presets) into the
    current one. The last saved entry wins for duplicate IDs, keeping the
    first entry's position. Incoming entries then override saved values.
    Invalid incoming IDs are rejected before the import writes any data;
    unrelated saved entries, including legacy malformed records, are kept."""
    merged, by_id = [], {}
    for item in current if isinstance(current, list) else []:
        item_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(item_id, str) and item_id:
            if item_id in by_id:
                merged[by_id[item_id]] = item
                continue
            by_id[item_id] = len(merged)
        merged.append(item)
    for item in incoming:
        item_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("Preset entries must have a non-empty string id")
        if item_id in by_id:
            merged[by_id[item_id]] = item
        else:
            by_id[item_id] = len(merged)
            merged.append(item)
    return merged


def preview_import_payload(body: dict, user: str, memory_manager, skills_manager, preset_manager) -> dict:
    """Dry-run validation and diff summary for an /api/import JSON payload."""
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object")

    warnings: list[str] = []
    version = body.get("version")
    if version not in (None, 1):
        warnings.append(f"Unexpected backup schema version {version!r}; attempting best-effort merge.")

    summary: dict[str, dict] = {}

    # Memories dry-run
    if "memories" in body:
        if not isinstance(body["memories"], list):
            warnings.append("'memories' must be a list; it will be ignored on import.")
        else:
            try:
                existing = memory_manager.load_all_for_update()
            except MemoryStoreUnreadable as e:
                logger.error("Refusing to preview memory import: %s", e)
                raise HTTPException(503, "Memory store is temporarily unreadable ? nothing can be imported.")
            existing_texts = {
                e.get("text", "").strip().lower()
                for e in existing
                if isinstance(e, dict) and e.get("owner") == user
            }
            seen = set(existing_texts)
            to_add = 0
            skipped = 0
            for mem in body["memories"]:
                if not isinstance(mem, dict) or not str(mem.get("text") or "").strip():
                    skipped += 1
                    continue
                norm = mem["text"].strip().lower()
                if norm in seen:
                    skipped += 1
                    continue
                seen.add(norm)
                to_add += 1
            summary["memories"] = {"total_in_file": len(body["memories"]), "to_add": to_add, "duplicates_or_invalid_skipped": skipped}

    # Skills dry-run
    if "skills" in body:
        if not isinstance(body["skills"], list):
            warnings.append("'skills' must be a list; it will be ignored on import.")
        else:
            existing = skills_manager.load_all()
            own = [s for s in existing if isinstance(s, dict) and s.get("owner") == user]
            existing_names = {s.get("name") for s in own if s.get("name")}
            existing_ids = {s.get("id") for s in own if s.get("id")}
            existing_titles = {
                (s.get("title") or s.get("description") or "").strip().lower()
                for s in own
            }
            to_add = 0
            skipped = 0
            for skill in body["skills"]:
                if not isinstance(skill, dict):
                    skipped += 1
                    continue
                title = (
                    skill.get("title") or skill.get("description")
                    or skill.get("name") or ""
                ).strip()
                if not title:
                    skipped += 1
                    continue
                sid = skill.get("id") or skill.get("name")
                nm = skill.get("name")
                if (sid and sid in existing_ids) or (nm and nm in existing_names) or (title.lower() in existing_titles):
                    skipped += 1
                    continue
                if nm:
                    existing_names.add(nm)
                if sid:
                    existing_ids.add(sid)
                existing_titles.add(title.lower())
                to_add += 1
            summary["skills"] = {"total_in_file": len(body["skills"]), "to_add": to_add, "duplicates_or_invalid_skipped": skipped}

    for section in ("presets", "settings", "features", "preferences"):
        if section in body:
            val = body[section]
            if not isinstance(val, dict):
                warnings.append(f"'{section}' must be an object; it will be ignored on import.")
            else:
                summary[section] = {"keys_to_merge": len(val), "keys": sorted(str(k) for k in val.keys())}

    has_changes = bool(summary)
    if not has_changes:
        warnings.append("No recognized backup sections found (expected memories, skills, presets, settings, features, or preferences).")

    return {
        "ok": has_changes,
        "version": version,
        "exported_at": body.get("exported_at"),
        "exported_by": body.get("exported_by"),
        "sections": summary,
        "warnings": warnings,
    }


def build_recovery_guidance(data_dir: Path | None = None, backup_dir: Path | None = None) -> dict:
    """Inspect persistent stores and backup archives and return actionable recovery guidance."""
    repo_root = Path(__file__).resolve().parent.parent
    d_dir = Path(data_dir) if data_dir is not None else (repo_root / "data")
    b_dir = Path(backup_dir) if backup_dir is not None else (repo_root / "backups")

    db_files = []
    json_files = []
    encryption_keys = []
    if d_dir.is_dir():
        for p in sorted(d_dir.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            rel = str(p.relative_to(d_dir))
            if p.suffix == ".db":
                db_files.append({"path": rel, "bytes": p.stat().st_size})
            elif p.suffix == ".json" and len(p.relative_to(d_dir).parts) == 1:
                json_files.append({"path": rel, "bytes": p.stat().st_size})
            elif "key" in p.name.lower() and p.suffix in {".key", ""}:
                encryption_keys.append(rel)

    snapshots = []
    if b_dir.is_dir():
        now_ts = datetime.now().timestamp()
        for p in b_dir.iterdir():
            if not p.is_file() or not p.name.endswith((".tar.gz", ".tgz")):
                continue
            st = p.stat()
            snapshots.append({
                "name": p.name,
                "path": str(p),
                "bytes": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "age_hours": round(max(0.0, (now_ts - st.st_mtime) / 3600.0), 1),
                "_mtime": st.st_mtime,
            })
        snapshots.sort(key=lambda s: s["_mtime"], reverse=True)
        for s in snapshots:
            s.pop("_mtime", None)

    warnings: list[str] = []
    if not snapshots:
        warnings.append("No full data/ snapshot archives found in backups/. Run 'python scripts/odysseus-backup snapshot' before upgrades or migrations.")
    elif snapshots[0]["age_hours"] > 168.0:
        warnings.append(f"Latest snapshot '{snapshots[0]['name']}' is {snapshots[0]['age_hours']}h old (> 7 days).")
    if db_files and not encryption_keys:
        warnings.append("No encryption key file detected under data/; ensure ODYSSEUS_SECRET_KEY / Fernet key is backed up separately so encrypted OAuth tokens remain readable after restore.")

    latest_name = snapshots[0]["name"] if snapshots else "backups/odysseus-backup-YYYYMMDD-HHMMSS.tar.gz"
    return {
        "data_dir": str(d_dir),
        "backup_dir": str(b_dir),
        "persistent_stores": {
            "sqlite_databases": db_files,
            "top_level_json": json_files,
            "encryption_key_files": encryption_keys,
        },
        "snapshots": snapshots,
        "warnings": warnings,
        "workflows": {
            "full_snapshot_cli": {
                "description": "Online SQLite-safe tarball snapshot and atomic stash-and-restore of the entire data/ directory.",
                "create_command": "python scripts/odysseus-backup snapshot",
                "verify_command": f"python scripts/odysseus-backup verify backups/{latest_name}" if not latest_name.startswith("backups/") else f"python scripts/odysseus-backup verify {latest_name}",
                "restore_command": f"python scripts/odysseus-backup restore backups/{latest_name} --yes" if not latest_name.startswith("backups/") else f"python scripts/odysseus-backup restore {latest_name} --yes",
                "safety_note": "Restore validates archive paths against traversal/symlinks and renames existing data/ to data.before-restore-<timestamp> before extracting.",
            },
            "portable_json_bundle": {
                "description": "Per-user portable JSON export/import for memories, skills, presets, settings, feature flags, and preferences.",
                "export_endpoint": "GET /api/export",
                "preview_endpoint": "POST /api/import/preview",
                "import_endpoint": "POST /api/import",
            },
        },
    }


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

        # Validate and prepare preset lists before importing any other section,
        # so a malformed ID cannot fail after memories or skills were written.
        restored_presets = None
        if isinstance(body.get("presets"), dict):
            restored_presets = dict(preset_manager.get_all())
            for key, value in body["presets"].items():
                if key in ("user_templates", "group_presets"):
                    if not isinstance(value, list):
                        raise HTTPException(400, f"Invalid {key}: expected a list")
                    try:
                        restored_presets[key] = _merge_by_id(restored_presets.get(key), value)
                    except ValueError as exc:
                        raise HTTPException(400, f"Invalid {key}: {exc}") from exc
                elif isinstance(value, (dict, list)):
                    restored_presets[key] = value

        imported = []

        # ── Memories ──
        if "memories" in body and isinstance(body["memories"], list):
            # Strict load: importing on top of an unreadable store would write
            # only the incoming rows back and drop everything already saved.
            try:
                existing = memory_manager.load_all_for_update()
            except MemoryStoreUnreadable as e:
                logger.error("Refusing to import memories: %s", e)
                raise HTTPException(
                    503, "Memory store is temporarily unreadable — nothing was imported."
                )
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
            # Dedup against THIS user's own skills only. Using every tenant's
            # rows (load_all) meant a skill whose id/name/title matched any
            # other user's was silently skipped, so the importing user lost
            # their own data — same cross-tenant bug fixed for memories above.
            # The full store is still saved back below.
            own = [s for s in existing if s.get("owner") == user]
            existing_names = {s.get("name") for s in own if s.get("name")}
            existing_ids = {s.get("id") for s in own if s.get("id")}
            existing_titles = {
                (s.get("title") or s.get("description") or "").strip().lower()
                for s in own
            }
            added = 0
            for skill in body["skills"]:
                if not isinstance(skill, dict):
                    continue
                title = (
                    skill.get("title") or skill.get("description")
                    or skill.get("name") or ""
                ).strip()
                if not title:
                    continue
                sid = skill.get("id") or skill.get("name")
                if sid and sid in existing_ids:
                    continue
                nm = skill.get("name")
                if nm and nm in existing_names:
                    continue
                if title.lower() in existing_titles:
                    continue
                owner = skill.get("owner")
                if user and not owner:
                    owner = user
                # Skills live on disk as SKILL.md files; the old JSON-era
                # skills_manager.save() no longer exists. Write each new skill
                # via add_skill (source="user" skips auto-dedup — this is an
                # explicit backup restore).
                result = skills_manager.add_skill(
                    title=title,
                    name=skill.get("name"),
                    description=skill.get("description"),
                    problem=skill.get("problem", ""),
                    solution=skill.get("solution", ""),
                    steps=skill.get("steps"),
                    tags=skill.get("tags"),
                    source="user",
                    teacher_model=skill.get("teacher_model"),
                    confidence=skill.get("confidence", 0.8),
                    owner=owner,
                    category=skill.get("category", "general"),
                    when_to_use=skill.get("when_to_use"),
                    procedure=skill.get("procedure"),
                    pitfalls=skill.get("pitfalls"),
                    verification=skill.get("verification"),
                    platforms=skill.get("platforms"),
                    requires_toolsets=skill.get("requires_toolsets"),
                    fallback_for_toolsets=skill.get("fallback_for_toolsets"),
                    status=skill.get("status", "draft"),
                    version=skill.get("version", "1.0.0"),
                )
                if result.get("_deduped"):
                    continue
                if result.get("name"):
                    existing_names.add(result["name"])
                if result.get("id"):
                    existing_ids.add(result["id"])
                existing_titles.add(title.lower())
                added += 1
            imported.append(f"{added} skills")

        # ── Presets ──
        if restored_presets is not None:
            preset_manager.save(restored_presets)
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
            from routes.prefs_routes import _update_off_loop
            # Replace only the keys the backup contains, as one locked update
            # on a worker thread. Copying the whole record out first and
            # writing it back would revert a Settings save made in between.
            # As everywhere in this store, a null value removes the key.
            await _update_off_loop(user, body["preferences"])
            imported.append("preferences")

        if not imported:
            return {"ok": False, "message": "No recognized data found in the file"}

        return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}


    @router.post("/api/import/preview")
    async def preview_import_data(request: Request):
        """Dry-run an import JSON bundle and report additions, duplicates, and warnings."""
        require_admin(request)
        user = get_current_user(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")
        return preview_import_payload(body, user, memory_manager, skills_manager, preset_manager)

    @router.get("/api/recovery/status")
    async def get_recovery_status(request: Request):
        """Inspect persistent data stores, available snapshots, and step-by-step recovery guidance."""
        require_admin(request)
        return build_recovery_guidance()

    return router
