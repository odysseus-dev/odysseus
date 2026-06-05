"""Regression: research CLI summary must tolerate a non-string query.

`_summarize` did `(data.get("query") or "")[:200]`. A non-string query from a
legacy/corrupt research JSON is truthy, so `123[:200]` raised TypeError.
"""
from tests.helpers.cli_loader import load_script


def _load_cli():
    return load_script("odysseus-research")


def test_preview_text_ignores_non_string():
    cli = _load_cli()
    assert cli._preview_text(None) == ""
    assert cli._preview_text(123) == ""
    assert cli._preview_text(["x"]) == ""
    assert cli._preview_text("q" * 250) == "q" * 200


def test_summarize_does_not_crash_on_non_string_query():
    cli = _load_cli()
    out = cli._summarize("rp1", {"query": 123, "status": "done"})
    assert out["query"] == ""
    assert out["id"] == "rp1"
