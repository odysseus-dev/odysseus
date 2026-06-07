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


def test_chat_attachment_picker_allows_any_file_type():
    file_input = _inputs()["file-input"]

    assert file_input["type"] == "file"
    assert "multiple" in file_input
    assert "accept" not in file_input
