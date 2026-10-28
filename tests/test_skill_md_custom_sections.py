"""SKILL.md sections Odysseus does not model must survive a save.

``parse_body`` maps the four known ``##`` headings (When to use, Procedure,
Pitfalls, Verification) onto fields and puts everything else in
``body_extra``. It used to drop the heading line of any other section, so
``## Overview`` or ``## Examples`` vanished the first time the file was
rewritten and their content ran into the paragraph above. That rewrite
happens on every markdown save, every ``update_skill`` call and right after
a skill is imported from a URL, which flattened most third-party skills.
"""

import textwrap

from services.memory.skill_format import Skill, parse_body
from services.memory.skills import SkillsManager


SKILL_MD = textwrap.dedent("""\
    ---
    name: pdf-forms
    description: Fill PDF forms
    ---

    # PDF forms

    Use this when the user hands you a fillable PDF.

    ## Overview

    Forms come in two flavours.

    ## When to use

    User asks to fill a form.

    ## Procedure

    1. Detect fields
    2. Fill them

    ## Examples

    - W-9
    - I-9
    """)


def test_parse_body_keeps_unknown_headings_in_body_extra():
    body = SKILL_MD.split("---\n", 2)[2]
    sections = parse_body(body)

    assert sections["when_to_use"] == "User asks to fill a form."
    assert sections["procedure"] == ["Detect fields", "Fill them"]
    extra = sections["body_extra"]
    assert "## Overview\n\nForms come in two flavours." in extra
    assert "## Examples\n\n- W-9\n- I-9" in extra
    # The free-form sections stay in their original order.
    assert extra.index("# PDF forms") < extra.index("## Overview") < extra.index("## Examples")


def test_known_headings_are_not_duplicated_into_body_extra():
    sections = parse_body(SKILL_MD.split("---\n", 2)[2])
    assert "## Procedure" not in sections["body_extra"]
    assert "## When to use" not in sections["body_extra"]


def test_rewritten_markdown_keeps_custom_sections():
    out = Skill.from_markdown(SKILL_MD).to_markdown()

    assert "## Overview\n\nForms come in two flavours." in out
    assert "## Examples\n\n- W-9\n- I-9" in out
    assert "## Procedure\n\n1. Detect fields\n2. Fill them" in out


def test_second_rewrite_does_not_change_the_body():
    once = Skill.from_markdown(SKILL_MD)
    twice = Skill.from_markdown(once.to_markdown())

    assert twice.body_extra == once.body_extra
    assert twice.procedure == once.procedure
    assert twice.when_to_use == once.when_to_use


def test_imported_bundle_keeps_custom_sections(tmp_path):
    mgr = SkillsManager(str(tmp_path))
    result = mgr.import_bundle_from_files({"pdf-forms/SKILL.md": SKILL_MD}, owner="alice")

    with open(result["path"], encoding="utf-8") as f:
        written = f.read()
    assert "## Overview" in written
    assert "## Examples" in written


def test_update_skill_keeps_custom_sections(tmp_path):
    mgr = SkillsManager(str(tmp_path))
    result = mgr.import_bundle_from_files({"pdf-forms/SKILL.md": SKILL_MD}, owner="alice")

    assert mgr.update_skill(result["name"], {"status": "published"}, owner="alice")

    with open(result["path"], encoding="utf-8") as f:
        written = f.read()
    assert "status: published" in written
    assert "## Overview\n\nForms come in two flavours." in written
    assert "## Examples\n\n- W-9\n- I-9" in written


def test_free_text_is_not_folded_into_the_last_list(tmp_path):
    # Plain notes used to be written after the Pitfalls list and read back
    # as part of its last item: "forgetting changelog Always run ...".
    mgr = SkillsManager(str(tmp_path))
    mgr.add_skill(name="release", description="Cut a release", source="user",
                  procedure=["bump version", "tag"], pitfalls=["forgetting changelog"])
    note = "Always run the smoke suite on staging first."

    assert mgr.update_skill("release", {"body_extra": note})
    assert mgr.update_skill("release", {"status": "published"})

    skill = mgr.load()[0]
    assert skill["pitfalls"] == ["forgetting changelog"]
    assert skill["procedure"] == ["bump version", "tag"]
    assert skill["body_extra"] == note
