"""Public skills tool behavior using isolated on-disk fixtures."""
import asyncio
import json

import pytest

from services.memory.skills import SkillsManager
from src.tools.system import do_manage_skills


def call(action, owner="sft_skill_fixture", **args):
    return asyncio.run(do_manage_skills(json.dumps({"action": action, **args}), owner=owner))


def test_publish_does_not_claim_success_when_visible_skill_is_not_writable(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    manager = SkillsManager(str(tmp_path))
    manager.add_skill(name="shared-fixture", description="Shared read-only fixture",
                      procedure=["Say hello"], status="draft", source="builtin", owner=None)
    before = call("view", name="shared-fixture")
    assert "status: draft" in before["results"]
    result = call("publish", name="shared-fixture")
    assert result.get("exit_code") == 1
    assert "error" in result
    assert call("view", name="shared-fixture") == before


def test_publish_owned_draft_saves_state_without_promising_automatic_approval(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    SkillsManager(str(tmp_path)).add_skill(name="owned-fixture", description="Owned fixture",
        procedure=["Report a value"], status="draft", owner="sft_skill_fixture")
    result = call("publish", name="owned-fixture")
    assert "error" not in result
    assert "status: published" in call("view", name="owned-fixture")["results"]
    assert "subject to" in result["results"]


@pytest.mark.parametrize("replacement", [None, 1, [], {}])
def test_invalid_patch_replacement_returns_error_without_losing_skill(tmp_path, monkeypatch, replacement):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    SkillsManager(str(tmp_path)).add_skill(name="owned-fixture", description="Unique old description",
        procedure=["Report a value"], status="draft", owner="sft_skill_fixture")
    before = call("view", name="owned-fixture")
    result = call("patch", name="owned-fixture", old_string="Unique old description", new_string=replacement)
    assert result.get("exit_code") == 1
    assert "string" in result["error"]
    assert call("view", name="owned-fixture") == before


def test_patch_followup_preserves_other_sections_and_rejects_ambiguous_edits(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    SkillsManager(str(tmp_path)).add_skill(name="owned-fixture", description="Summary alpha",
        procedure=["Report alpha"], verification=["Check alpha"], status="draft", owner="sft_skill_fixture")
    before = call("view", name="owned-fixture")
    assert call("patch", name="owned-fixture", old_string="alpha", new_string="beta")["exit_code"] == 1
    assert call("view", name="owned-fixture") == before
    assert "error" not in call("patch", name="owned-fixture", old_string="Report alpha", new_string="Report gamma")
    after = call("view", name="owned-fixture")["results"]
    assert "Report gamma" in after and "Report alpha" not in after
    assert "Summary alpha" in after and "Check alpha" in after
    assert "error" not in call("patch", name="owned-fixture", old_string="Report gamma", new_string="Report alpha")
    assert call("view", name="owned-fixture") == before


def test_reference_followup_reads_only_owner_visible_confined_files(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    SkillsManager(str(tmp_path)).add_skill(name="owned-fixture", description="Reference fixture",
        procedure=["Read references/details.md"], category="general", status="draft", owner="sft_skill_fixture")
    references = tmp_path / "skills" / "general" / "owned-fixture" / "references"
    references.mkdir()
    (references / "details.md").write_text("Unique reference detail: violet-72", encoding="utf8")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_MUST_NOT_BE_RETURNED", encoding="utf8")
    (references / "escape.md").symlink_to(outside)
    assert "references/details.md" in call("view", name="owned-fixture")["results"]
    assert call("view_ref", name="owned-fixture", path="references/details.md")["results"] == "Unique reference detail: violet-72"
    for path in ("references/escape.md", "../../../outside.txt", str(outside)):
        result = call("view_ref", name="owned-fixture", path=path)
        assert result.get("exit_code") == 1
        assert "OUTSIDE_MUST_NOT_BE_RETURNED" not in str(result)
    assert call("view_ref", owner="other-owner", name="owned-fixture", path="references/details.md")["exit_code"] == 1


def test_view_does_not_substitute_skill_body_for_requested_reference(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    SkillsManager(str(tmp_path)).add_skill(name="owned-fixture", description="Main body, not reference",
        procedure=["Read references/details.md"], status="draft", owner="sft_skill_fixture")
    references = tmp_path / "skills" / "general" / "owned-fixture" / "references"
    references.mkdir()
    (references / "details.md").write_text("Reference-only value: violet-72", encoding="utf8")
    before = call("view", name="owned-fixture")
    wrong_action = call("view", name="owned-fixture", path="references/details.md")
    assert wrong_action.get("exit_code") == 1
    assert "view_ref" in wrong_action["error"]
    assert "results" not in wrong_action
    assert call("view_ref", name="owned-fixture", path="references/details.md")["results"] == "Reference-only value: violet-72"
    assert call("view", name="owned-fixture") == before
