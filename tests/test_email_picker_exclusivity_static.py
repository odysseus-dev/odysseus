from pathlib import Path
import re
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parent.parent


def test_email_folder_and_filter_pickers_are_exclusive_and_escape_safe():
    source = email_library_source()

    assert source.count("const filterMenu = document.getElementById('email-filter-menu');") == 1
    assert source.count("const folderMenu = document.getElementById('email-folder-menu');") == 1
    assert source.count("e.stopImmediatePropagation?.();") >= 2
    assert "filterMenu?._dismiss?.();" in source
    assert "folderMenu?._dismiss?.();" in source
    escape_handler = source[source.index("state._libInnerEscHandler = (e) => {"):source.index("window.addEventListener('keydown', state._libInnerEscHandler, true)")]
    assert "if (dismissTopMenu()) {" in escape_handler
    assert "window.addEventListener('keydown', state._libInnerEscHandler, true)" in source
    inbox = (ROOT / "static/js/emailInbox.js").read_text(encoding="utf-8")
    assert re.search(r"from './emailLibrary\.js\?v=[A-Za-z0-9_-]+'", inbox)
