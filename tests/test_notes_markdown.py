"""core.notes_markdown: task lines inside note content.

The vectors in tests/fixtures/notes_task_vectors.json are shared with the JS
mirror (tests/notesMarkdownTasks.test.mjs) and the interactive renderer
(tests/notesMarkdownRender.test.mjs), so the three cannot drift on which line
is task N.
"""
import json
from pathlib import Path

import pytest

from core.notes_markdown import (
    append_task_line,
    apply_items_to_content,
    derive_note_type,
    effective_content,
    fold_image_url,
    has_tasks,
    image_urls,
    items_to_markdown,
    keep_task_lines,
    merge_items_into_content,
    parse_task_lines,
    pending_task_summary,
    reminder_body,
    strip_task_lines,
    toggle_task,
)

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "notes_task_vectors.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", VECTORS["parse"], ids=lambda c: c["name"])
def test_parse_vectors(case):
    assert parse_task_lines(case["content"]) == case["tasks"]
    assert has_tasks(case["content"]) is case["has_tasks"]
    assert derive_note_type(None, case["content"]) == case["note_type"]


@pytest.mark.parametrize("case", VECTORS["toggle"], ids=lambda c: c["name"])
def test_toggle_vectors(case):
    if case.get("error"):
        with pytest.raises(IndexError):
            toggle_task(case["content"], case["index"])
        return
    assert toggle_task(case["content"], case["index"]) == (case["content_after"], case["item"])


@pytest.mark.parametrize("case", VECTORS["apply_items"], ids=lambda c: c["name"])
def test_apply_items_vectors(case):
    assert apply_items_to_content(case["content"], case["items"]) == case["content_after"]


@pytest.mark.parametrize("case", VECTORS["append"], ids=lambda c: c["name"])
def test_append_vectors(case):
    assert append_task_line(case["content"], case["text"], case["indent"]) == (case["content_after"], case["item"])


@pytest.mark.parametrize("case", VECTORS["merge"], ids=lambda c: c["name"])
def test_merge_vectors(case):
    assert merge_items_into_content(case["content"], case["items"]) == case["content_after"]


def test_apply_items_none_means_not_supplied():
    assert apply_items_to_content("- [ ] keep\ntext", None) == "- [ ] keep\ntext"


def test_kept_task_lines_land_after_an_unclosed_fence():
    # A text-only write whose new text leaves a fence open must not put the
    # old tasks back inside it, where they stop being tasks.
    kept = keep_task_lines("- [ ] milk", "notes\n```\ncode")
    assert kept == "notes\n```\ncode\n```\n\n- [ ] milk"
    assert [t["text"] for t in parse_task_lines(kept)] == ["milk"]


def test_append_rejects_blank_text():
    with pytest.raises(ValueError):
        append_task_line("x", "   ")


def test_items_to_markdown_skips_junk_and_reads_checked():
    items = [{"text": "a", "checked": True}, "junk", None, {"text": ""}, {"text": "b", "indent": 1}]
    assert items_to_markdown(items) == "- [x] a\n  - [ ] b"


def test_derive_note_type_keeps_goal_and_draw():
    assert derive_note_type("goal", "Ship it\n\n- [ ] step") == "goal"
    assert derive_note_type("draw", "![](/api/upload/d)") == "draw"
    assert derive_note_type("todo", "- [ ] a\n\n- [x] b") == "checklist"
    assert derive_note_type("checklist", "Intro\n- [ ] a") == "note"
    assert derive_note_type("todo", "") == "note"
    # A fenced sample is something the user wrote: not a pure checklist.
    assert derive_note_type("note", "- [ ] a\n```\nx\n```") == "note"


def test_strip_task_lines_keeps_prose_and_fenced_samples():
    content = "Goal\n\n- [ ] a\n\n\n- [x] b\n```\n- [ ] sample\n```"
    assert strip_task_lines(content) == "Goal\n\n```\n- [ ] sample\n```"


def test_pending_summary_and_reminder_body():
    content = "Buy:\n" + "\n".join(f"- [ ] item {i}" for i in range(10)) + "\n- [x] done"
    summary = pending_task_summary(content)
    assert summary.startswith("Pending (10):\n- item 0\n")
    assert summary.endswith("- item 7\n...and 2 more")
    assert reminder_body(content) == "Buy:\n\n" + summary
    assert reminder_body("- [x] a") == "1 item"
    assert reminder_body("Plain " * 100).startswith("Plain")
    assert len(reminder_body("x" * 900)) == 400
    assert pending_task_summary("no tasks") == ""


def test_effective_content_folds_unmigrated_items_only_when_readable():
    assert effective_content("Text", '[{"text": "a"}]') == "Text\n\n- [ ] a"
    assert effective_content("Text", "{bad json") == "Text"
    assert effective_content(None, None) == ""


def test_legacy_image_helpers():
    from core.notes_markdown import markdown_image_url, remove_image_line
    assert markdown_image_url(" /a b(1).png ") == "/a%20b%281%29.png"
    assert remove_image_line("```\n![](/a)\n```", "/a") == "```\n![](/a)\n```"
    assert remove_image_line("Body\n\n![](/a)", "/a") == "Body"
    assert remove_image_line("![](/a)\nBody", "/a") == "Body"
    assert remove_image_line("Body", "/a") == "Body"
    assert remove_image_line("![](/a)", "/a") == ""


def test_fold_image_url_appends_once_and_replaces_drawings():
    assert fold_image_url("Text", "/api/upload/a") == "Text\n\n![](/api/upload/a)"
    assert fold_image_url("![](/api/upload/a)", "/api/upload/a") == "![](/api/upload/a)"
    assert fold_image_url("", "/api/upload/a") == "![](/api/upload/a)"
    assert fold_image_url("x", "  ") == "x"
    assert fold_image_url("![](/api/upload/old)\nnote", "/api/upload/new", replace="/api/upload/old") == "![](/api/upload/new)\nnote"
    assert fold_image_url(
        '![Receipt](/api/upload/old "May")\nOther ![photo](/api/upload/old)',
        "/api/upload/new",
        replace="/api/upload/old",
    ) == '![Receipt](/api/upload/new "May")\nOther ![photo](/api/upload/old)'
    # A fenced sample mentioning the URL is not a reference to it.
    assert fold_image_url("```\n![](/api/upload/a)\n```", "/api/upload/a").endswith("\n\n![](/api/upload/a)")
    assert image_urls('![a](/api/upload/1 "t") and ![](/api/upload/2)') == ["/api/upload/1", "/api/upload/2"]


@pytest.mark.parametrize("content, urls", [
    ('![a](/u "t")', ["/u"]),
    ("![a](/u  \"t\" )", []),
    ("![a](/u x)", []),
    ("![](/a)![](/b)", ["/a", "/b"]),
    ("![a [b] c](/u)", []),
    ("![a[](/u)", ["/u"]),
    ("![](![](/u)", ["![](/u"]),
    ("![x]( /u)", []),
    ("![x](/u", []),
    ("![x\n](/u)", []),
    ('![x](/u "t\n")', []),
])
def test_image_urls_edge_cases(content, urls):
    assert image_urls(content) == urls


@pytest.mark.parametrize("text, plain", [
    ("see [docs](https://x) now", "see docs now"),
    ("![pic](/u) and [a](b)", "and a"),
    ("![a](b c) [d]( e f )", "d"),
    ("![x[y](z)", ""),
    ("[a]([b](c)", "a"),
    ("[unclosed](x", "[unclosed](x"),
    ("!\\[a](b)", "!\\a"),
])
def test_reminder_text_drops_images_and_keeps_link_text(text, plain):
    from core.notes_markdown import reminder_text
    assert reminder_text(text) == plain


@pytest.mark.parametrize("unit", ["![", "![](!", "![](a ", '![](a "', "[", "[](", "![]("])
def test_image_and_link_scans_are_linear_on_unclosed_markup(unit):
    """A long line of markup that never closes used to cost one rescan of the
    rest of the line per start (minutes on 100 KB); it must stay linear.
    Locally these take 0.1 to 25 ms; the bound leaves room for a loaded runner."""
    import time
    from core.notes_markdown import reminder_text
    content = unit * (100_000 // len(unit))
    started = time.perf_counter()
    image_urls(content)
    reminder_text(content)
    assert time.perf_counter() - started < 1.0
