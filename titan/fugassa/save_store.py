"""Fugassa save slot CRUD + SQLite bootstrap."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.db import sqlite_store
from titan.fugassa.db import asset_repository, seed as db_seed
from titan.fugassa.game_bootstrap import (
    apply_wizard_draft,
    attach_portrait_from_staging,
    build_initial_game_state,
    resolve_theme,
    write_game_json,
    write_gm_guides,
    wizard_portrait_staging_path,
)
from titan.fugassa.paths import SAVES_DIR, FUGASSA_ROOT, ensure_save_dirs, generated_dir

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_NAME_LEN = 80


class SaveStoreError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def ensure_layout() -> None:
    os.makedirs(SAVES_DIR, exist_ok=True)
    os.makedirs(FUGASSA_ROOT, exist_ok=True)


def _iso_mtime(path: str) -> str | None:
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except OSError:
        return None


def normalize_save_name(name: str) -> str:
    cleaned = (name or "").strip()
    cleaned = _INVALID_NAME.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        raise SaveStoreError("Save name cannot be empty", "invalid_name")
    if len(cleaned) > _MAX_NAME_LEN:
        raise SaveStoreError(f"Save name max {_MAX_NAME_LEN} characters", "invalid_name")
    return cleaned


def save_dir(save_id: str) -> str:
    return os.path.join(SAVES_DIR, save_id)


def game_db_path(save_id: str) -> str:
    return os.path.join(save_dir(save_id), "game.db")


def save_id_from_db_path(db_path: str) -> str | None:
    """Derive save slot id from a game.db path (`.../saves/<id>/game.db`)."""
    if not db_path:
        return None
    base = os.path.basename(os.path.dirname(os.path.abspath(db_path)))
    return base or None


def _save_exists(save_id: str) -> bool:
    path = save_dir(save_id)
    return os.path.isdir(path)


def _build_save_entry(save_id: str) -> dict[str, Any]:
    path = save_dir(save_id)
    db_path = game_db_path(save_id)
    meta = sqlite_store.read_campaign_meta(db_path) if os.path.isfile(db_path) else None
    display_name = (meta or {}).get("campaign_name") or save_id
    return {
        "id": save_id,
        "name": display_name,
        "folder": save_id,
        "updated_at": _iso_mtime(db_path) or _iso_mtime(path),
        "has_db": os.path.isfile(db_path),
        "turn_number": (meta or {}).get("turn_number", 0),
        "theme": (meta or {}).get("theme"),
    }


def list_saves() -> list[dict[str, Any]]:
    ensure_layout()
    saves: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(SAVES_DIR))
    except OSError:
        return saves

    for name in names:
        path = os.path.join(SAVES_DIR, name)
        if not os.path.isdir(path):
            continue
        saves.append(_build_save_entry(name))
    return saves


def get_save(save_id: str) -> dict[str, Any]:
    if not _save_exists(save_id):
        raise SaveStoreError("Save not found", "not_found")
    entry = _build_save_entry(save_id)
    db_path = game_db_path(save_id)
    meta = sqlite_store.read_campaign_meta(db_path)
    if meta:
        entry["campaign"] = meta
    return entry


def create_save(name: str, *, theme: str = "fantasy") -> dict[str, Any]:
    ensure_layout()
    save_id = normalize_save_name(name)
    if _save_exists(save_id):
        raise SaveStoreError("A save with this name already exists", "duplicate")

    path = save_dir(save_id)
    os.makedirs(path, exist_ok=True)
    ensure_save_dirs(path)

    sqlite_store.init_game_db(game_db_path(save_id), save_id, theme=theme)
    return get_save(save_id)


def create_save_from_wizard(draft: dict[str, Any]) -> dict[str, Any]:
    """Create save slot and hydrate game.json + SQLite kanon from wizard draft."""
    ensure_layout()
    theme = resolve_theme(draft)
    name = str(draft.get("world_name") or "").strip()
    if not name:
        raise SaveStoreError("World name is required", "invalid_name")

    save_id = normalize_save_name(name)
    if _save_exists(save_id):
        raise SaveStoreError("A save with this name already exists", "duplicate")

    path = save_dir(save_id)
    os.makedirs(path, exist_ok=True)
    ensure_save_dirs(path)

    db_path = game_db_path(save_id)
    sqlite_store.init_game_db(db_path, save_id, theme=theme)

    state = build_initial_game_state(save_id, theme)
    state = apply_wizard_draft(state, draft, theme=theme)

    portrait_path = str(draft.get("character_portrait_path") or "").strip()
    if not portrait_path:
        portrait_path = wizard_portrait_staging_path()
    portrait_rel = attach_portrait_from_staging(state, path, portrait_path)

    gm_map = draft.get("gm_guides_map")
    if isinstance(gm_map, dict) and gm_map:
        write_gm_guides(path, gm_map)

    sqlite_store.update_campaign_from_wizard(db_path, draft=draft, theme=theme)

    from titan.fugassa.player_portrait_prompt import resolve_portrait_prompts_from_sources

    pos_prompt, neg_prompt, combined_prompt = resolve_portrait_prompts_from_sources(
        draft=draft,
        game_state=state,
    )
    pos_prompt = pos_prompt or None
    neg_prompt = neg_prompt or None
    if combined_prompt:
        draft["portrait_sd_prompt_text"] = combined_prompt
        snap = state.setdefault("wizard_draft_snapshot", {})
        if isinstance(snap, dict):
            snap["portrait_sd_prompt_text"] = combined_prompt
            appearance = snap.get("portrait_appearance")
            if isinstance(appearance, dict):
                if pos_prompt:
                    appearance["positive_prompt"] = pos_prompt
                if neg_prompt:
                    appearance["negative_prompt"] = neg_prompt

    sql_seed = db_seed.bootstrap_from_wizard(
        db_path,
        draft=draft,
        state=state,
        portrait_relative_path=portrait_rel,
        portrait_prompt=pos_prompt,
        portrait_negative_prompt=neg_prompt,
    )
    asset_repository.rebuild_manifest(db_path, generated_dir(path))

    # Mirror the SQL seed's sublocation wiring onto the runtime state *before*
    # writing game.json — otherwise a game that starts "inside" a room (per
    # `starting_location_from_opening`'s "Parent (Sub)" convention) would have
    # a real sublocation graph in SQL but game.json's `player`/`location_state`
    # would still look like standing in the open, so `leave_sublocation` and
    # location-scoped tracking (e.g. Investigate) couldn't see it.
    sublocation_id = sql_seed.get("sublocation_id")
    if sublocation_id:
        player = dict(state.get("player") or {})
        player["sublocation_id"] = int(sublocation_id)
        player["sublocation_anchor"] = {
            "map_code": "overworld",
            "x": int(player.get("x", 0)),
            "y": int(player.get("y", 0)),
            "z": int(player.get("z", 0)),
        }
        state["player"] = player
        location_state = dict(state.get("location_state") or {})
        location_state["location_id"] = int(sublocation_id)
        state["location_state"] = location_state

    write_game_json(path, state)

    entry = get_save(save_id)
    entry["has_game_json"] = True
    entry["sql_seed"] = sql_seed
    return entry


def rename_save(save_id: str, new_name: str) -> dict[str, Any]:
    if not _save_exists(save_id):
        raise SaveStoreError("Save not found", "not_found")

    new_id = normalize_save_name(new_name)
    if new_id == save_id:
        return get_save(save_id)
    if _save_exists(new_id):
        raise SaveStoreError("A save with this name already exists", "duplicate")

    old_path = save_dir(save_id)
    new_path = save_dir(new_id)
    os.rename(old_path, new_path)

    db_path = game_db_path(new_id)
    sqlite_store.update_campaign_name(db_path, new_id)

    return get_save(new_id)


def is_name_available(name: str, *, exclude_id: str | None = None) -> bool:
    try:
        save_id = normalize_save_name(name)
    except SaveStoreError:
        return False
    if exclude_id and save_id == exclude_id:
        return True
    return not _save_exists(save_id)


def copy_save(source_id: str, dest_id: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Deep-copy a save slot (game.db, game.json, generated/, gm/, autosave_prev/)."""
    if not _save_exists(source_id):
        raise SaveStoreError("Source save not found", "not_found")
    dest_id = normalize_save_name(dest_id)
    if dest_id == source_id:
        raise SaveStoreError("Source and destination must differ", "invalid_name")
    src_path = save_dir(source_id)
    dest_path = save_dir(dest_id)
    if _save_exists(dest_id):
        if not overwrite:
            raise SaveStoreError("Destination save already exists", "duplicate")
        shutil.rmtree(dest_path)
    shutil.copytree(src_path, dest_path)
    _mirror_tree_ownership(src_path, dest_path)
    return get_save(dest_id)


def _mirror_tree_ownership(src_root: str, dest_root: str) -> None:
    """When copying as root on a bind mount, mirror source uid/gid so the app can write."""
    try:
        root_stat = os.stat(src_root)
    except OSError:
        return
    uid, gid = root_stat.st_uid, root_stat.st_gid

    def _apply(path: str) -> None:
        try:
            os.chown(path, uid, gid)
        except OSError:
            return

    _apply(dest_root)
    for dirpath, dirnames, filenames in os.walk(dest_root):
        for name in dirnames:
            _apply(os.path.join(dirpath, name))
        for name in filenames:
            _apply(os.path.join(dirpath, name))


def delete_save(save_id: str) -> None:
    if not _save_exists(save_id):
        raise SaveStoreError("Save not found", "not_found")
    shutil.rmtree(save_dir(save_id))
