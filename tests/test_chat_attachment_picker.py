from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _InputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attr_map = dict(attrs)
        input_id = attr_map.get("id")
        if input_id:
            self.inputs[input_id] = attr_map


def _inputs():
    parser = _InputParser()
    parser.feed((ROOT / "static" / "index.html").read_text(encoding="utf-8"))
    return parser.inputs


def _static_source(*parts):
    return (ROOT / "static" / Path(*parts)).read_text(encoding="utf-8")


def test_chat_attachment_picker_allows_any_file_type():
    file_input = _inputs()["file-input"]

    assert file_input["type"] == "file"
    assert "multiple" in file_input
    assert "accept" not in file_input


def test_chat_attachment_picker_resets_for_same_file_reselect():
    file_handler = _static_source("js", "fileHandler.js")
    app = _static_source("app.js")

    assert "input.value = '';" in file_handler
    assert "e.target.value = '';" in app


def test_attachment_strip_drop_queues_files():
    app = _static_source("app.js")
    start = app.index("attachStrip.addEventListener('drop'")
    end = app.index("attachStrip.addEventListener('dragleave'", start)
    drop_handler = app[start:end]

    assert "fileHandlerModule.addFiles(files)" in drop_handler
    assert "fileHandlerModule.renderAttachStrip()" in drop_handler
