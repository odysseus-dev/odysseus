"""Tests for the unified-note markdown task-line helpers.

These back the toggle-by-index contract shared between the REST API, the agent
tool, and the JS frontend, so they pin down indexing, round-tripping, and the
legacy items -> markdown migration path.
"""

import importlib.machinery
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    # Import the module directly so we don't pull in the heavy core package
    # __init__ (which imports the LLM stack).
    path = ROOT / "core" / "notes_markdown.py"
    loader = importlib.machinery.SourceFileLoader("notes_markdown", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


nm = _load()


def test_parse_task_lines_indices_and_indent():
    content = "# Groceries\nBuy stuff\n- [ ] Milk\n- [x] Bread\n  - [ ] Whole wheat"
    tasks = nm.parse_task_lines(content)
    assert [t["index"] for t in tasks] == [0, 1, 2]
    assert [t["text"] for t in tasks] == ["Milk", "Bread", "Whole wheat"]
    assert [t["done"] for t in tasks] == [False, True, False]
    assert tasks[2]["indent"] == 1


def test_parse_ignores_non_task_lines():
    assert nm.parse_task_lines("just text\n- a bullet\n* another") == []
    assert nm.parse_task_lines("") == []
    assert nm.parse_task_lines(None) == []


def test_has_tasks():
    assert nm.has_tasks("- [ ] x") is True
    assert nm.has_tasks("- plain") is False


def test_toggle_task_flips_and_preserves_rest():
    content = "intro\n- [ ] one\n- [x] two"
    new, item = nm.toggle_task(content, 0)
    assert item["done"] is True
    assert "- [x] one" in new
    assert "- [x] two" in new  # untouched
    assert new.startswith("intro\n")

    back, item2 = nm.toggle_task(new, 1)
    assert item2["done"] is False
    assert "- [ ] two" in back


def test_empty_task_lines_do_not_merge():
    # Regression: an empty task line must not swallow the following line.
    tasks = nm.parse_task_lines("- [ ]\n- [ ] second")
    assert [t["text"] for t in tasks] == ["", "second"]
    assert len(nm.parse_task_lines("- [ ]\n- [ ]")) == 2


def test_toggle_item_whose_text_contains_checkbox_syntax():
    # Regression: an item literally named "- [x]" must still toggle by its real
    # (leading) checkbox, not be fooled by the brackets in its text.
    new, item = nm.toggle_task("- [ ] - [x]", 0)
    assert item["done"] is True
    assert new == "- [x] - [x]"
    back, item2 = nm.toggle_task(new, 0)
    assert item2["done"] is False
    assert back == "- [ ] - [x]"


def test_toggle_task_out_of_range():
    import pytest
    with pytest.raises(IndexError):
        nm.toggle_task("- [ ] only", 5)


def test_toggle_preserves_trailing_newline():
    assert nm.toggle_task("- [ ] x\n", 0)[0].endswith("\n")
    assert not nm.toggle_task("- [ ] x", 0)[0].endswith("\n")


def test_fenced_code_task_lines_are_not_tasks():
    # `- [ ]` lines inside a ``` fence are a code sample, not a checklist. The
    # browser renderer shows them verbatim, so the helpers must ignore them too —
    # otherwise migration/toggle/list diverge from what the user sees.
    content = (
        "real list:\n"
        "- [ ] Milk\n"
        "```\n"
        "- [ ] not a task\n"
        "- [x] also code\n"
        "```\n"
        "- [x] Bread\n"
    )
    tasks = nm.parse_task_lines(content)
    assert [t["text"] for t in tasks] == ["Milk", "Bread"]
    assert [t["index"] for t in tasks] == [0, 1]
    assert nm.has_tasks("```\n- [ ] code only\n```") is False


def test_tilde_fence_and_long_fence_also_ignored():
    # Tilde fences and >3-char fences are honoured the same way.
    assert nm.parse_task_lines("~~~\n- [ ] code\n~~~") == []
    assert nm.has_tasks("````\n- [ ] code\n````") is False
    # A shorter same-char run inside an open fence is content, not a close.
    content = "````\n```\n- [ ] still code\n````\n- [ ] real"
    assert [t["text"] for t in nm.parse_task_lines(content)] == ["real"]


def test_toggle_skips_fenced_code_when_indexing():
    # Index 0 is the only real task; the fenced look-alike must not be toggled
    # and must not shift the index of the real one.
    content = "```\n- [ ] code\n```\n- [ ] real"
    new, item = nm.toggle_task(content, 0)
    assert item["text"] == "real"
    assert "- [x] real" in new
    assert "- [ ] code" in new  # the code sample is untouched


def test_merge_appends_when_only_fenced_code_has_tasks():
    # The data-loss case: a note whose only task-looking lines are inside a fence
    # has no real tasks, so legacy `items` must still be folded in (not dropped).
    content = "How to:\n```\n- [ ] example\n```"
    out = nm.merge_items_into_content(content, [{"text": "Real"}])
    assert out.endswith("- [ ] Real")
    assert "```" in out  # original code sample preserved


def test_items_to_markdown():
    md = nm.items_to_markdown([
        {"text": "A", "done": True},
        {"text": "B", "indent": 1},
        {"text": "", "done": False},  # skipped
    ])
    assert md == "- [x] A\n  - [ ] B"


def test_merge_items_into_content_appends_to_text():
    out = nm.merge_items_into_content("Some text", [{"text": "X"}])
    assert out == "Some text\n\n- [ ] X"


def test_merge_is_noop_when_content_already_has_tasks():
    # Content is authoritative; a redundant items array must not double up.
    out = nm.merge_items_into_content("- [ ] keep", [{"text": "X"}])
    assert out == "- [ ] keep"


def test_merge_into_empty_content():
    assert nm.merge_items_into_content("", [{"text": "X", "done": True}]) == "- [x] X"
    assert nm.merge_items_into_content(None, None) == ""
