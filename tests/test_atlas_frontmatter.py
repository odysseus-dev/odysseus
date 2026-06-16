"""Frontmatter + section extraction for the Atlas Bases engine."""

from src.atlas_frontmatter import parse_frontmatter, extract_sections


def test_parse_frontmatter_basic():
    props, body = parse_frontmatter("---\nstatus: open\ntype: meeting\n---\n# Hi\nbody")
    assert props == {"status": "open", "type": "meeting"}
    assert body == "# Hi\nbody"


def test_parse_frontmatter_absent():
    props, body = parse_frontmatter("# No frontmatter\ntext")
    assert props == {}
    assert body == "# No frontmatter\ntext"


def test_parse_frontmatter_malformed_is_tolerant():
    md = "---\nstatus: : : broken\n  - nope\n---\nbody"
    props, body = parse_frontmatter(md)
    assert props == {}            # bad YAML → empty, not an exception
    assert body == md             # body left untouched


def test_parse_frontmatter_non_mapping():
    props, body = parse_frontmatter("---\n- just\n- a list\n---\nx")
    assert props == {}            # a YAML list isn't properties


def test_extract_sections_levels_and_bodies():
    md = "# Top\nintro\n## Todo\n- a\n- b\n## Notes\ndone"
    secs = extract_sections(md)
    assert [(s["heading"], s["level"]) for s in secs] == [("Top", 1), ("Todo", 2), ("Notes", 2)]
    todo = next(s for s in secs if s["heading"] == "Todo")
    assert todo["body"] == "- a\n- b"


def test_extract_sections_ignores_frontmatter():
    secs = extract_sections("---\ntitle: x\n---\n# Only Heading\nbody")
    assert [s["heading"] for s in secs] == ["Only Heading"]
