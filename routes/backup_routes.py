\"\"\"Backup routes — export/import user data (memories, presets, settings, skills, preferences).\"\"\"

import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.settings import load_settings, save_settings, load_features, save_features
from src.constants import DATA_DIR, UPLOAD_DIR, PERSONAL_DIR

logger = logging.getLogger(__name__)


def setup_backup_routes(memory_manager, preset_manager, skills_manager) -> APIRouter:
    router = APIRouter(tags=["backup"])

    @router.get("/api/export")
    async def export_data(request: Request):
        """Export all user data as a full .tar.gz backup archive."""
        require_admin(request)
        user = get_current_user(request)

        # 1. Create a temp directory for the backup
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # 2. Export JSON metadata (memories, skills, settings, features, preferences)
            memories = memory_manager.load(owner=user)
            presets = preset_manager.get_all()
            skills = skills_manager.load(owner=user)
            settings = load_settings()
            features = load_features()
            from routes.prefs_routes import _load_for_user
            preferences = _load_for_user(user)

            meta_data = {
                "version": 2,  # Binary archive version
                "exported_at": datetime.now().isoformat(),
                "exported_by": user,
                "memories": memories,
                "presets": presets,
                "skills": skills,
                "settings": settings,
                "features": features,
                "preferences": preferences,
            }
            (tmp_path / "metadata.json").write_text(json.dumps(meta_data, indent=2, ensure_ascii=False))

            # 3. Snapshot the SQLite database
            db_path = Path(DATA_DIR) / "app.db"
            if db_path.exists():
                # Safe live snapshot using sqlite3 backup API
                backup_db = tmp_path / "app.db"
                try:
                    src = sqlite3.connect(str(db_path))
                    dst = sqlite3.connect(str(backup_db))
                    with dst:
                        src.backup(dst)
                    dst.close()
                    src.close()
                except Exception as e:
                    logger.error(f"DB snapshot failed: {e}")

            # 4. Include uploads and personal docs
            for dname, dpath in [("uploads", UPLOAD_DIR), ("personal_docs", PERSONAL_DIR)]:
                src_dir = Path(dpath)
                if src_dir.exists():
                    dst_dir = tmp_path / dname
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True, 
                                    ignore=shutil.ignore_patterns('*.tmp', 'cache'))

            # 5. Create the tarball
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"odysseus_backup_{timestamp}.tar.gz"
            archive_path = Path(tempfile.gettempdir()) / archive_name
            
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(tmp_path, arcname="")

            return FileResponse(
                path=archive_path,
                filename=archive_name,
                media_type="application/gzip"
            )

    @router.post("/api/import")
    async def import_data(request: Request, file: UploadFile = File(...)):
        """Import user data from a previously exported archive (.tar.gz) or legacy JSON file."""
        require_admin(request)
        user = get_current_user(request)
        
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                import_file = tmp_path / "import.tmp"
                
                with import_file.open("wb") as f:
                    shutil.copyfileobj(file.file, f)

                # Case 1: Archive (.tar.gz)
                if tarfile.is_tarfile(import_file):
                    with tarfile.open(import_file, "r:gz") as tar:
                        tar.extractall(path=tmp_path)
                    
                    metadata_file = tmp_path / "metadata.json"
                    if not metadata_file.exists():
                        raise HTTPException(400, "Invalid backup: metadata.json missing")
                    
                    body = json.loads(metadata_file.read_text())
                    
                    # Restore files
                    restored_files = []
                    for dname, dpath in [("uploads", UPLOAD_DIR), ("personal_docs", PERSONAL_DIR)]:
                        src_dir = tmp_path / dname
                        if src_dir.exists():
                            shutil.copytree(src_dir, dpath, dirs_exist_ok=True)
                            restored_files.append(dname)
                
                # Case 2: Legacy JSON
                else:
                    import_file.seek(0)
                    try:
                        body = json.loads(import_file.read_text())
                        restored_files = []
                    except Exception:
                        raise HTTPException(400, "File is neither a valid tar archive nor JSON")

                if not isinstance(body, dict):
                    raise HTTPException(400, "Expected a JSON object in metadata/file")

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
                    existing_names = {s.get("name") for s in existing if s.get("name")}
                    existing_ids = {s.get("id") for s in existing if s.get("id")}
                    existing_titles = {
                        (s.get("title") or s.get("description") or "").strip().lower()
                        for s in existing
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
                if "presets" in body and isinstance(body["presets"], dict):
                    current = preset_manager.get_all()
                    current.update(body["presets"])
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

                if restored_files:
                    imported.append(f"files ({', '.join(restored_files)})")

                if not imported:
                    return {"ok": False, "message": "No new data found in the backup"}

                return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}

        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Import failed")
            raise HTTPException(500, detail=str(e))

    return router

