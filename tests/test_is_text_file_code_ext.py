"""Code/text attachments must be recognized as text, not routed to office.

_is_text_file gates whether build_user_content extracts an attached file as
text. It only listed .txt/.py/.html/.md/.json/.csv/.log/.js, while
_process_text_file renders ~25 code extensions. So an attached .go/.rs/.ts/
.sql/.cpp/.java/etc. failed this gate, fell through to office processing,
and was replaced by an "[Attached document file]" stub — the source code
never reached the model.
"""
import pytest

from src.document_processor import _is_text_file


@pytest.mark.parametrize("name", [
    "main.go", "lib.rs", "app.ts", "comp.tsx", "util.jsx", "style.css",
    "query.sql", "a.cpp", "b.c", "C.java", "x.rb", "y.php", "s.sh",
    "conf.yml", "conf.yaml", "data.xml",
])
def test_code_extensions_are_text(name):
    assert _is_text_file(name) is True


@pytest.mark.parametrize("name", ["notes.txt", "script.py", "page.html", "readme.md", "app.js"])
def test_existing_extensions_still_text(name):
    assert _is_text_file(name) is True


@pytest.mark.parametrize("name", ["photo.png", "doc.pdf", "sheet.xlsx", "archive.zip"])
def test_binary_extensions_not_text(name):
    assert _is_text_file(name) is False
