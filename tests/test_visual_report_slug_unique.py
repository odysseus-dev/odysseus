"""Regression: _extract_headings must emit a unique slug per heading.

_make_slug disambiguates repeats by appending "-N", but it only tracked the
*base* slug, so a generated "intro-1" could collide with a naturally-occurring
"intro-1" (e.g. headings "Intro", "Intro", "Intro 1" all produced
["intro", "intro-1", "intro-1"]). Duplicate slugs become duplicate heading ids,
which makes the second table-of-contents link dead. Slugs are now guaranteed
unique. Plain repeats keep their existing "-1", "-2" sequence.
"""
from src.visual_report import _extract_headings


def _slugs(md):
    return [h["slug"] for h in _extract_headings(md)]


def test_disambiguated_slug_does_not_collide_with_natural_slug():
    slugs = _slugs("## Intro\n\n## Intro\n\n## Intro 1\n")
    assert len(slugs) == len(set(slugs)), slugs


def test_plain_repeats_keep_sequential_suffixes():
    assert _slugs("## Foo\n\n## Foo\n\n## Foo\n") == ["foo", "foo-1", "foo-2"]


def test_distinct_headings_are_unchanged():
    assert _slugs("## Alpha\n\n## Beta\n") == ["alpha", "beta"]
