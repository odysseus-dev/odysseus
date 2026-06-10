"""Test that SkillsManager preserves metadata fields."""
import os
import shutil
from pathlib import Path
from services.memory.skills import SkillsManager

def test_add_skill_preserves_metadata(tmp_path):
    # Mock data directory
    data_dir = tmp_path / "data" / "skills"
    data_dir.mkdir(parents=True)
    
    # Initialize manager with tmp_path
    manager = SkillsManager(data_dir=str(data_dir))
    
    # Create a skill with metadata
    created_at = "2026-06-01T10:00:00"
    last_used_at = 1717516800
    
    skill = manager.add_skill(
        title="Metadata Skill",
        problem="Test problem",
        solution="Test solution",
        created=created_at,
        uses=5,
        last_used=last_used_at,
        body_extra="Some extra notes"
    )
    
    assert skill["created"] == created_at
    assert skill["uses"] == 5
    assert skill["last_used"] == last_used_at
    assert skill["body_extra"] == "Some extra notes"
    
    # Verify it's actually in the usage sidecar
    usage = manager._load_usage()
    key = manager._usage_key(skill["name"], None)
    assert usage[key]["uses"] == 5
    assert usage[key]["last_used"] == last_used_at

def test_skill_format_uses_notes_header():
    from services.memory.skill_format import emit_body
    
    sections = {
        "procedure": ["Step 1"],
        "body_extra": "Additional notes here"
    }
    
    body = emit_body(sections)
    assert "## Notes" in body
    assert "## Procedure" in body
    assert "Additional notes here" in body
