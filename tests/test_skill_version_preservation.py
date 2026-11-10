"""Issue #2131: editing a skill must not reinterpret its version as a number."""

import pytest

from services.memory.skill_format import Skill, parse_frontmatter
from services.memory.skills import SkillsManager


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.10", "1.10"),
        ("2.20", "2.20"),
        ("01", "01"),
        ("0", "0"),
        ("1.0.0", "1.0.0"),
        ("true", "true"),
        ("null", "null"),
        ('"1.10"', "1.10"),
        ("'2.20'", "2.20"),
        ('"release\\u002d01"', "release-01"),
    ],
)
def test_version_survives_repeated_markdown_saves(raw, expected):
    markdown = f"---\nname: demo\nversion: {raw}\nconfidence: 0.80\n---\n"
    for _ in range(3):
        skill = Skill.from_markdown(markdown)
        assert skill.version == expected
        assert skill.confidence == 0.8
        markdown = skill.to_markdown()


def test_version_preservation_keeps_other_frontmatter_types():
    fm, _ = parse_frontmatter(
        "---\nversion: 1.10\nconfidence: 0.80\nuses: 3\n"
        "enabled: true\ntags: [one, two]\n---\n"
    )
    assert fm == {
        "version": "1.10", "confidence": 0.8, "uses": 3,
        "enabled": True, "tags": ["one", "two"],
    }


@pytest.mark.parametrize("version_line", ["", "version:\n", 'version: ""\n'])
def test_missing_or_empty_version_retains_default(version_line):
    skill = Skill.from_markdown(f"---\nname: demo\n{version_line}---\n")
    assert skill.version == "1.0.0"


def test_version_survives_unrelated_edits_on_disk(tmp_path):
    manager = SkillsManager(str(tmp_path))
    manager.add_skill(name="demo", version="1.10", owner="alice")
    for i in range(3):
        assert manager.update_skill("demo", {"description": f"Edit {i}"}, owner="alice")
        stored = manager.load(owner="alice")
        assert stored[0]["version"] == "1.10"
        assert stored[0]["description"] == f"Edit {i}"
    markdown = next((tmp_path / "skills").rglob("SKILL.md")).read_text(encoding="utf-8")
    assert "version: 1.10\n" in markdown
