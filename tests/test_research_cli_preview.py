"""Regression: research CLI summary must tolerate a non-string query.

`_summarize` did `(data.get("query") or "")[:200]`. A non-string query from a
legacy/corrupt research JSON is truthy, so `123[:200]` raised TypeError.
"""
import importlib.machinery
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-research"
    loader = importlib.machinery.SourceFileLoader("odysseus_research_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
