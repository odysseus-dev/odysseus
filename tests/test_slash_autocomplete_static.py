"""Static regressions for slash autocomplete command-group expansion."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_AC = (_REPO / "static" / "js" / "slashAutocomplete.js").read_text(encoding="utf-8")


def test_exact_parent_command_expands_subcommands_before_top_level_row_cap():
    assert "function _exactCommandGroupItems" in _AC
    assert "entry.token.toLowerCase().startsWith(prefix)" in _AC
    assert "items = groupItems.slice(0, MAX_VISIBLE);" in _AC


def test_setup_group_has_room_for_chatgpt_subscription_suggestion():
    assert "const MAX_VISIBLE = 14;" in _AC
