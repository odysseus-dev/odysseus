"""SKILL.md frontmatter must round-trip non-ASCII text losslessly (#5210).

_emit_scalar used json.dumps with the default ensure_ascii=True, writing every
non-ASCII char as \\uXXXX into a UTF-8 file; _parse_scalar stripped the quotes
but never decoded the escapes. Each save re-escaped the backslashes, so the
description degraded exponentially across load/save cycles.
"""
from services.memory.skill_format import (
    _emit_scalar,
    _parse_scalar,
    emit_frontmatter,
    parse_frontmatter,
)

UMLAUT = "Einstiegs- und Prüfungslinie für AGB"
MIXED = "café ☕ 日本語 — naïve résumé"


def _roundtrip_fm(fm):
    body = emit_frontmatter(fm)
    parsed, _ = parse_frontmatter("---\n" + body + "\n---\n\n# Body\n")
    return parsed


def test_scalar_emits_literal_utf8_not_escaped():
    out = _emit_scalar(UMLAUT)
    assert "\\u" not in out
    assert "Prüfungslinie" in out


def test_scalar_roundtrips_non_ascii():
    for s in (UMLAUT, MIXED):
        assert _parse_scalar(_emit_scalar(s)) == s


def test_frontmatter_roundtrips_non_ascii():
    fm = {"name": "agb", "description": MIXED}
    assert _roundtrip_fm(fm)["description"] == MIXED


def test_repeated_save_load_is_stable():
    # The core regression: the description must not drift or accumulate
    # backslashes across many save/load cycles.
    fm = {"name": "agb", "description": UMLAUT}
    body = emit_frontmatter(fm)
    for _ in range(6):
        parsed, _ = parse_frontmatter("---\n" + body + "\n---\n\n# Body\n")
        assert parsed["description"] == UMLAUT
        body = emit_frontmatter(parsed)


def test_legacy_escaped_value_heals_on_load():
    # A file written by the old ensure_ascii=True emitter decodes correctly now.
    assert _parse_scalar('"Pr\\u00fcfung"') == "Prüfung"


def test_double_corrupted_value_heals_one_level_per_load():
    # File content: "Pr\\u00fcfung"  (backslash already doubled by a prior
    # corrupt save). First load removes one backslash level; second decodes.
    once = _parse_scalar('"Pr\\\\u00fcfung"')
    assert once == "Pr\\u00fcfung"
    assert _parse_scalar('"' + once + '"') == "Prüfung"


def test_single_quoted_scalar_stays_literal():
    # Single-quoted values are hand-authored, not JSON — keep them verbatim.
    assert _parse_scalar("'a: b'") == "a: b"


def test_embedded_quotes_and_backslashes_roundtrip():
    tricky = 'a "quoted" path C:\\Users\\x with: a colon'
    assert _parse_scalar(_emit_scalar(tricky)) == tricky
