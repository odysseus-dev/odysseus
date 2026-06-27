"""Issue #3207 — newly created characters missing from Group participant dropdown.

The fix has two parts:
1. group.js _getCharacterList() merges in-memory userTemplates from presets.js
   as a fallback (covers the gap while the async templates API save is in-flight).
2. presets.js saveCustomPreset() does an optimistic in-memory update of
   userTemplates immediately on success (bridges the timing race where
   loadUserTemplates hasn't been triggered yet).

These tests assert the source patterns exist so they can't be silently removed.
"""
from pathlib import Path

GROUP_JS = Path("static/js/group.js").read_text(encoding="utf-8")
PRESETS_JS = Path("static/js/presets.js").read_text(encoding="utf-8")


# --- group.js: in-memory template merge in _getCharacterList ---

def test_group_imports_getUserTemplates():
    """group.js must import getUserTemplates from presets.js."""
    assert "getUserTemplates" in GROUP_JS
    assert "from './presets.js'" in GROUP_JS or 'from "./presets.js"' in GROUP_JS


def test_group_merges_in_memory_templates():
    """_getCharacterList must call getUserTemplates() and merge results."""
    assert "getUserTemplates()" in GROUP_JS
    # The merge loop should check for duplicates by id
    assert "!chars.find(c => c.id === t.id)" in GROUP_JS


# --- presets.js: optimistic in-memory update on save ---

def test_presets_exports_getUserTemplates():
    """getUserTemplates must be exported from presets.js."""
    assert "export function getUserTemplates()" in PRESETS_JS


def test_presets_optimistic_update_on_save():
    """saveCustomPreset must update userTemplates in-memory before the async POST."""
    # Find the optimistic update block
    assert "Optimistically update the in-memory templates list" in PRESETS_JS
    # Must push to userTemplates for new entries
    assert "userTemplates.push(_entry)" in PRESETS_JS
    # Must Object.assign for existing entries
    assert "Object.assign(_existing, _entry)" in PRESETS_JS


def test_presets_getUserTemplates_returns_array():
    """getUserTemplates should return a shallow copy of userTemplates."""
    assert "return [...userTemplates]" in PRESETS_JS


def test_presets_optimistic_id_not_empty():
    """Optimistic update must generate a client-side id for new characters (not empty string)."""
    # The id generation uses 'user-' prefix matching server's uuid convention
    assert "user-' + Math.random" in PRESETS_JS
    # Must NOT use empty string as fallback (that was the bug)
    assert "(_existing && _existing.id) || ''" not in PRESETS_JS

def test_presets_clone_happens_before_mutation():
    """Rollback snapshot must be taken before Object.assign mutates _existing."""
    clone_idx = PRESETS_JS.find("clone = JSON.parse(JSON.stringify(_existing))")
    assign_idx = PRESETS_JS.find("Object.assign(_existing, _entry)")

    assert clone_idx != -1
    assert assign_idx != -1
    assert clone_idx < assign_idx

def test_presets_rollbak_restores_from_clone():
    """Failed save must restore the original object from the pre-mutation clone."""
    assert "if (clone)" in PRESETS_JS
    assert "Object.assign(_existing, clone)" in PRESETS_JS

def test_presets_clone_is_deep_copy():
    """Rollback snapshot must be a deep clone, not an alias."""
    assert "clone = JSON.parse(JSON.stringify(_existing))" in PRESETS_JS

def test_presets_no_alias_clone():
    """Prevent accidental rollback breakage via reference assignment."""
    assert "clone = _existing" not in PRESETS_JS
    assert "const clone = _existing" not in PRESETS_JS
    assert "let clone = _existing" not in PRESETS_JS