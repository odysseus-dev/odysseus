import os
from types import SimpleNamespace

import pytest
from PIL import Image

from src.agent_tools import ocr_engine
from src.agent_tools.ocr_engine import _join_line, _prepare_image, _reading_text, extract_image_text
from src.chat_handler import ChatHandler
from src.document_processor import _process_pdf


def _entry(text, x, y, width, height=20):
    return {
        "t": text,
        "p": 0.9,
        "min_x": x,
        "max_x": x + width,
        "min_y": y,
        "max_y": y + height,
        "cy": y + height / 2,
        "h": height,
    }


def test_reading_text_joins_rows_and_spaces():
    entries = [
        _entry("Hello", 0, 0, 40),
        _entry("Odysseus", 50, 0, 70),
        _entry("12345", 130, 0, 40),
        _entry("Second", 0, 40, 50),
        _entry("line", 60, 40, 30),
    ]
    assert _reading_text(entries) == "Hello Odysseus 12345\nSecond line"


def test_join_line_handles_punctuation_and_cjk():
    assert _join_line(["Hello", ",", "world", "!"]) == "Hello, world!"
    assert _join_line(["Total", ":", "$1,234.50"]) == "Total: $1,234.50"
    assert _join_line(["你好", "世界"]) == "你好世界"


def test_prepare_image_downscales_and_cleans_up(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (3000, 1200), "white").save(source)
    monkeypatch.setenv("ODYSSEUS_OCR_MAX_SIDE", "500")

    read_path, temp_path = _prepare_image(source)

    assert temp_path
    try:
        with Image.open(read_path) as img:
            assert max(img.size) == 500
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def test_extract_image_text_returns_merged_text(tmp_path, monkeypatch):
    class _Result:
        boxes = [
            [[0, 0], [40, 0], [40, 20], [0, 20]],
            [[50, 0], [120, 0], [120, 20], [50, 20]],
        ]
        txts = ["Hello", "world"]
        scores = [0.99, 0.98]

    monkeypatch.setattr(ocr_engine, "_prepare_image", lambda path: (str(path), None))
    monkeypatch.setattr(ocr_engine, "_engine", lambda: (lambda path: _Result()))

    out = extract_image_text(tmp_path / "any.png")

    assert out["text"] == "Hello world"
    assert out["lines"][0]["t"] == "Hello"
    assert out["lines"][1]["t"] == "world"


class _ImageUploadHandler:
    def __init__(self, path):
        self.path = path

    def resolve_upload(self, att_id, owner=None):
        return {
            "id": att_id,
            "name": "scan.png",
            "mime": "image/png",
            "size": 123,
            "width": 100,
            "height": 50,
            "path": self.path,
        }

    def is_image_file(self, *_args, **_kwargs):
        return True


def _make_handler(tmp_path):
    return ChatHandler(
        session_manager=None,
        memory_manager=None,
        chat_processor=None,
        research_handler=None,
        preset_manager=None,
        upload_handler=_ImageUploadHandler(str(tmp_path / "scan.png")),
    )


@pytest.mark.asyncio
async def test_chat_falls_back_to_local_ocr_without_vision(monkeypatch, tmp_path):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True if key == "vision_enabled" else "")
    monkeypatch.setattr("src.chat_handler.model_supports_vision", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "src.chat_handler.analyze_image_with_vl_result",
        lambda *_args, **_kwargs: {
            "text": "[No vision model configured — set one in Settings → Vision]",
            "model": "",
        },
    )

    async def _ocr(_path):
        return "INVOICE\nTotal: 42"

    monkeypatch.setattr("src.chat_handler._local_ocr_text", _ocr)
    monkeypatch.setattr(
        "src.chat_handler.build_user_content",
        lambda enhanced, *_args, **_kwargs: enhanced,
    )

    sess = SimpleNamespace(model="text-only", endpoint_url="", owner="user", id="session")
    enhanced, user_content, text_ctx, youtube, meta = await _make_handler(tmp_path).preprocess_message(
        "what does this say?", ["att-1"], sess, auto_opened_docs=[]
    )

    assert "Local OCR text" in enhanced
    assert "INVOICE" in enhanced
    assert user_content == enhanced
    assert text_ctx == enhanced
    assert youtube == []
    assert meta[0]["vision_model"] == "local-ocr"


@pytest.mark.asyncio
async def test_chat_reports_when_ocr_finds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True if key == "vision_enabled" else "")
    monkeypatch.setattr("src.chat_handler.model_supports_vision", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "src.chat_handler.analyze_image_with_vl_result",
        lambda *_args, **_kwargs: {"text": "[VL model unavailable - image not analyzed]", "model": ""},
    )

    async def _ocr(_path):
        return ""

    monkeypatch.setattr("src.chat_handler._local_ocr_text", _ocr)
    monkeypatch.setattr(
        "src.chat_handler.build_user_content",
        lambda enhanced, *_args, **_kwargs: enhanced,
    )

    sess = SimpleNamespace(model="text-only", endpoint_url="", owner="user", id="session")
    enhanced, _user_content, _text_ctx, _youtube, _meta = await _make_handler(tmp_path).preprocess_message(
        "what does this say?", ["att-1"], sess, auto_opened_docs=[]
    )

    assert "Local OCR found no readable text" in enhanced


@pytest.mark.asyncio
async def test_chat_ocs_when_vision_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: False if key == "vision_enabled" else default,
    )

    async def _ocr(_path):
        return "DISABLED VISION OCR"

    monkeypatch.setattr("src.chat_handler._local_ocr_text", _ocr)
    monkeypatch.setattr(
        "src.chat_handler.build_user_content",
        lambda enhanced, *_args, **_kwargs: enhanced,
    )

    sess = SimpleNamespace(model="text-only", endpoint_url="", owner="user", id="session")
    enhanced, _user_content, _text_ctx, _youtube, meta = await _make_handler(tmp_path).preprocess_message(
        "what does this say?", ["att-1"], sess, auto_opened_docs=[]
    )

    assert "vision is disabled" in enhanced.lower()
    assert "DISABLED VISION OCR" in enhanced
    assert meta[0]["vision_model"] == "local-ocr"


def test_scanned_pdf_uses_local_ocr_when_no_text_layer(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_path = tmp_path / "scan.pdf"
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    monkeypatch.setattr("src.document_processor._local_ocr_text", lambda _path: "SCANNED PAGE")

    out = _process_pdf(str(pdf_path))

    assert "[Page 1 OCR text]" in out
    assert "SCANNED PAGE" in out
