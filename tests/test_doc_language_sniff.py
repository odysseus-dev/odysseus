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
