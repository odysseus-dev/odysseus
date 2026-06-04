import textwrap

from services.memory.skill_format import Skill


def test_skill_version_preserves_trailing_zero_on_round_trip():
    markdown = textwrap.dedent("""\
        ---
        name: versioned-skill
        description: keeps semver-like versions intact
        version: 1.10
        category: general
        status: draft
        confidence: 0.8
        source: learned
        created: 2026-01-01T00:00:00Z
        ---

        ## When to Use

        Test skill version parsing.
        """)

    skill = Skill.from_markdown(markdown)
    rendered = skill.to_markdown()

    assert skill.version == "1.10"
    assert "version: 1.10" in rendered
    assert Skill.from_markdown(rendered).version == "1.10"
