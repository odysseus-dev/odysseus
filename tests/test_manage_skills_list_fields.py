import json

import pytest

from services.memory.skill_format import Skill
from services.memory.skills import SkillsManager
from src.tools.system import do_manage_skills


MULTILINE_PROCEDURE = """1. Open the existing file.
   Keep related context visible.

2. Apply the minimal patch.
   ```python
   print("still one step")
   ```

3. Run the focused test."""


def test_add_skill_normalizes_plain_string_procedure_without_character_entries(tmp_path):
    sm = SkillsManager(str(tmp_path))

    entry = sm.add_skill(
        name="plain-string-procedure",
        description="plain string procedure",
        procedure=MULTILINE_PROCEDURE,
        tags=["dev"],
        owner="alice",
        source="user",
    )

    assert entry["procedure"] == [
        'Open the existing file.\nKeep related context visible.',
        'Apply the minimal patch.\n```python\nprint("still one step")\n```',
        "Run the focused test.",
    ]
    assert entry["procedure"] != list(MULTILINE_PROCEDURE)

    markdown = sm.read_skill_md("plain-string-procedure", owner="alice")
    assert markdown is not None
    assert "\n1. O\n2. p\n3. e\n" not in markdown
    assert 'print("still one step")' in markdown

    reparsed = Skill.from_markdown(markdown)
    assert reparsed.procedure == entry["procedure"]


def test_update_skill_normalizes_plain_string_procedure_without_character_entries(tmp_path):
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="update-string-procedure",
        description="before",
        procedure=["before"],
        owner="alice",
        source="user",
    )

    assert sm.update_skill(
        "update-string-procedure",
        {"procedure": MULTILINE_PROCEDURE},
        owner="alice",
    )

    updated = sm.load(owner="alice")[0]
    assert updated["procedure"][0] == "Open the existing file.\nKeep related context visible."
    assert updated["procedure"] != list(MULTILINE_PROCEDURE)
    markdown = sm.read_skill_md("update-string-procedure", owner="alice")
    assert markdown is not None
    assert "\n1. O\n2. p\n3. e\n" not in markdown


def test_add_and_update_accept_canonical_list_fields(tmp_path):
    sm = SkillsManager(str(tmp_path))
    entry = sm.add_skill(
        name="canonical-lists",
        description="canonical",
        procedure=["one", "two"],
        pitfalls=["avoid this"],
        verification=["passes"],
        tags=["dev", "skill"],
        platforms=["linux"],
        requires_toolsets=["shell"],
        fallback_for_toolsets=["web"],
        owner="alice",
        source="user",
    )

    assert entry["procedure"] == ["one", "two"]
    assert entry["pitfalls"] == ["avoid this"]
    assert entry["verification"] == ["passes"]
    assert entry["tags"] == ["dev", "skill"]
    assert entry["platforms"] == ["linux"]
    assert entry["requires_toolsets"] == ["shell"]
    assert entry["fallback_for_toolsets"] == ["web"]

    assert sm.update_skill(
        "canonical-lists",
        {
            "steps": ["updated one", "updated two"],
            "tags": ["updated"],
            "platforms": [],
            "requires_toolsets": ["read_file"],
            "fallback_for_toolsets": [],
        },
        owner="alice",
    )
    updated = sm.load(owner="alice")[0]
    assert updated["procedure"] == ["updated one", "updated two"]
    assert updated["tags"] == ["updated"]
    assert updated["platforms"] == []
    assert updated["requires_toolsets"] == ["read_file"]
    assert updated["fallback_for_toolsets"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("procedure", {"bad": "dict"}),
        ("procedure", 123),
        ("procedure", [["nested"]]),
        ("procedure", ["ok", 3]),
        ("steps", "plain steps string"),
        ("pitfalls", {"bad": "dict"}),
        ("pitfalls", 123),
        ("pitfalls", [["nested"]]),
        ("pitfalls", ["ok", 3]),
        ("verification", {"bad": "dict"}),
        ("tags", {"bad": "dict"}),
        ("tags", 123),
        ("tags", [["nested"]]),
        ("tags", ["ok", 3]),
        ("platforms", ["linux", 3]),
        ("requires_toolsets", ["shell", ["nested"]]),
        ("fallback_for_toolsets", {"bad": "dict"}),
    ],
)
def test_add_skill_rejects_invalid_list_field_values(tmp_path, field, value):
    sm = SkillsManager(str(tmp_path))
    kwargs = {
        "name": f"invalid-{field.replace('_', '-')}",
        "description": "invalid",
        "procedure": ["valid procedure"],
        "owner": "alice",
        "source": "user",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        sm.add_skill(**kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("procedure", {"bad": "dict"}),
        ("procedure", 123),
        ("procedure", [["nested"]]),
        ("procedure", ["ok", 3]),
        ("steps", "plain steps string"),
        ("pitfalls", ["ok", 3]),
        ("verification", [["nested"]]),
        ("tags", {"bad": "dict"}),
        ("platforms", 123),
        ("requires_toolsets", ["shell", ["nested"]]),
        ("fallback_for_toolsets", ["web", 3]),
    ],
)
def test_update_skill_rejects_invalid_list_field_values(tmp_path, field, value):
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="invalid-update",
        description="invalid update",
        procedure=["valid procedure"],
        owner="alice",
        source="user",
    )

    with pytest.raises(ValueError, match=field):
        sm.update_skill("invalid-update", {field: value}, owner="alice")


@pytest.mark.asyncio
async def test_manage_skills_add_returns_sanitized_validation_error(monkeypatch, tmp_path):
    import src.constants as constants

    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    result = await do_manage_skills(
        json.dumps({
            "action": "add",
            "name": "invalid-tool-add",
            "description": "invalid tool add",
            "procedure": ["valid"],
            "tags": ["ok", 3],
            "status": "draft",
        }),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["error"] == "tags must be a list of strings"


@pytest.mark.asyncio
async def test_manage_skills_edit_rejects_invalid_frontmatter_list(monkeypatch, tmp_path):
    import src.constants as constants

    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="invalid-tool-edit",
        description="invalid tool edit",
        procedure=["valid"],
        owner="alice",
        source="user",
    )

    result = await do_manage_skills(
        json.dumps({
            "action": "edit",
            "name": "invalid-tool-edit",
            "content": """---
name: invalid-tool-edit
description: invalid tool edit
tags: [ok, 3]
status: draft
confidence: 0.8
source: user
owner: alice
created: 2026-07-12T00:00:00Z
---

## Procedure

1. valid
""",
        }),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["error"] == "Could not parse content as SKILL.md: tags must be a list of strings"


def test_skill_from_markdown_preserves_valid_list_round_trip():
    original = Skill(
        name="valid-round-trip",
        description="valid round trip",
        tags=["dev", "skill"],
        platforms=["linux"],
        requires_toolsets=["shell"],
        fallback_for_toolsets=["web"],
        procedure=["first", "second"],
        pitfalls=["avoid this"],
        verification=["passes"],
        owner="alice",
    )

    reparsed = Skill.from_markdown(original.to_markdown())

    assert reparsed.tags == ["dev", "skill"]
    assert reparsed.platforms == ["linux"]
    assert reparsed.requires_toolsets == ["shell"]
    assert reparsed.fallback_for_toolsets == ["web"]
    assert reparsed.procedure == ["first", "second"]
    assert reparsed.pitfalls == ["avoid this"]
    assert reparsed.verification == ["passes"]
