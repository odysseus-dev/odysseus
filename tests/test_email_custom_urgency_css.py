from pathlib import Path


STYLE_CSS = Path(__file__).resolve().parents[1] / "static" / "style.css"


def _brace_depth_at(css: str, target: int) -> int:
    """Return CSS block depth at target, ignoring comments and quoted strings."""
    depth = 0
    quote = ""
    in_comment = False
    escaped = False
    i = 0
    while i < target:
        char = css[i]
        next_char = css[i + 1] if i + 1 < target else ""
        if in_comment:
            if char == "*" and next_char == "/":
                in_comment = False
                i += 2
                continue
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char == "/" and next_char == "*":
            in_comment = True
            i += 2
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            assert depth >= 0, f"unexpected closing brace before offset {i}"
        i += 1
    return depth


def test_email_settings_media_query_closes_before_compose_and_notes_rules():
    css = STYLE_CSS.read_text(encoding="utf-8")
    compose_mobile = css.index('/* On mobile, the "New" (compose) button')
    notes = css.index("/* ── Notes ── */")

    assert _brace_depth_at(css, compose_mobile) == 0
    assert _brace_depth_at(css, notes) == 0
    assert _brace_depth_at(css, len(css)) == 0
