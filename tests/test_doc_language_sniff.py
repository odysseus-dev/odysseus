"""Document language detection (src/agent_tools/document_tools._sniff_doc_language).

Pins that simple code scripts are detected as code rather than falling to the
'markdown' default — the bug behind "AI-created python scripts show no syntax
highlighting" (a markdown-classified doc renders python as flat monochrome).
Prose and real markdown must stay 'markdown'.
"""

import pytest

from src.agent_tools.document_tools import _sniff_doc_language as sniff


@pytest.mark.parametrize("text", [
    'print("Hello, world!")\nfor i in range(5):\n    print(i)',  # simple script, no import/def
    'def main():\n    pass\n\nif __name__ == "__main__":\n    main()',
    'import os\nprint(os.getcwd())',
    'while True:\n    break',
    'for x in items:\n    total += x',
    '#!/usr/bin/env python3\nprint("hi")',
])
def test_python_scripts_detected(text):
    assert sniff(text) == "python"


@pytest.mark.parametrize("text", [
    'console.log("hi");\ndocument.querySelector("#x");',
    'const add = (a, b) => a + b;\nvar x = 5;',
])
def test_javascript_detected(text):
    assert sniff(text) == "javascript"


@pytest.mark.parametrize("text", [
    "This is a normal paragraph. I print things sometimes and go for walks.",
    "# Title\n\nSome **bold** text and a list:\n- one\n- two",
    "",
])
def test_prose_and_markdown_stay_markdown(text):
    assert sniff(text) == "markdown"


def test_unambiguous_markup_still_wins():
    assert sniff("<svg viewBox='0 0 10 10'></svg>") == "svg"
    assert sniff('{"a": 1, "b": [2, 3]}') == "json"


# --- _maybe_promote_language: doc language follows content, but only safely ----

from src.agent_tools.document_tools import _maybe_promote_language as promote


class _Doc:
    def __init__(self, language):
        self.language = language


def test_promote_raw_code_on_default_doc():
    d = _Doc("markdown")
    promote(d, "def add(x, y):\n    return x + y\n\nprint(add(1, 2))")
    assert d.language == "python"
    d = _Doc("")
    promote(d, "const f = (a, b) => a + b;\nconsole.log(f(1, 2));")
    assert d.language == "javascript"


def test_promote_skips_markdown_wrapped_code():
    # The "# heading + ```python fence" case — it's a markdown doc, keep it.
    d = _Doc("markdown")
    promote(d, "# Calculator\n```python\ndef add(x, y):\n    return x + y\n```")
    assert d.language == "markdown"


def test_promote_leaves_prose_and_explicit_languages():
    d = _Doc("markdown")
    promote(d, "# Notes\n\nSome **bold** prose.")
    assert d.language == "markdown"
    d = _Doc("python")               # explicitly set — never override
    promote(d, "# just a comment\nx = 1")
    assert d.language == "python"
    d = _Doc("email")
    promote(d, "def x(): pass")
    assert d.language == "email"
