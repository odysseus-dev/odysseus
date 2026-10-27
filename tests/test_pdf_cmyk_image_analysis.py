"""Actual embedded PDF images must reach vision as a decodable PNG."""
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
import pytest

from src import document_processor as dp


def make_pdf(tmp_path, mode):
    colors = {"CMYK": (0, 255, 255, 0), "RGB": (255, 0, 0), "L": 127}
    path = tmp_path / (mode + ".pdf")
    image = Image.new(mode, (128, 96), colors[mode])
    if mode == "CMYK":
        # A raw DeviceCMYK XObject avoids JPEG/Adobe inversion differences in
        # Pillow-generated PDFs; the extracted color samples are unambiguous.
        writer = PdfWriter()
        page = writer.add_blank_page(width=128, height=96)
        stream = DecodedStreamObject()
        stream.set_data(image.tobytes())
        stream.update({NameObject(key): value for key, value in {
            "/Type": NameObject("/XObject"), "/Subtype": NameObject("/Image"),
            "/ColorSpace": NameObject("/DeviceCMYK"), "/BitsPerComponent": NumberObject(8),
            "/Width": NumberObject(128), "/Height": NumberObject(96),
        }.items()})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"):
            DictionaryObject({NameObject("/Im0"): writer._add_object(stream)})})
        content = DecodedStreamObject()
        content.set_data(b"q 128 0 0 96 0 0 cm /Im0 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        writer.write(path)
    else:
        image.save(path, "PDF")
    assert PdfReader(path).pages[0].images[0].image.mode == mode
    return path


@pytest.mark.parametrize("mode,expected", [("CMYK", "RGB"), ("RGB", "RGB"), ("L", "L")])
def test_pdf_image_reaches_vision_with_owner_and_png_pixels(tmp_path, monkeypatch, mode, expected):
    pdf = make_pdf(tmp_path, mode)
    calls = []
    def analyze(path, owner=None):
        with Image.open(path) as image:
            image.load()
            assert image.format == "PNG" and image.mode == expected
            assert image.size == (128, 96)
            if mode == "CMYK":
                red, green, blue = image.getpixel((64, 48))
                assert red > 250 and green < 5 and blue < 5
            calls.append((Path(path), owner))
        return "Synthetic OCR result"
    monkeypatch.setattr(dp, "analyze_image_with_vl", analyze)
    output = dp._process_pdf(str(pdf), owner="alice")
    assert "[Page 1 image 1 text]: Synthetic OCR result" in output
    assert len(calls) == 1 and calls[0][1] == "alice"
    assert not calls[0][0].exists(), "Temporary PNG must be removed after analysis"
    assert PdfReader(pdf).pages[0].images[0].image.mode == mode, "Source PDF is unchanged"


def test_mixed_pdf_keeps_images_on_both_pages(tmp_path, monkeypatch):
    writer = PdfWriter()
    for mode in ("CMYK", "RGB"):
        writer.append(str(make_pdf(tmp_path, mode)))
    pdf = tmp_path / "mixed.pdf"
    writer.write(pdf)
    calls = []
    def analyze(path, owner=None):
        with Image.open(path) as image:
            image.load()
            calls.append(image.mode)
        return "Image content"
    monkeypatch.setattr(dp, "analyze_image_with_vl", analyze)
    output = dp._process_pdf(str(pdf), owner="alice")
    assert calls == ["RGB", "RGB"]
    assert "Page 1 image 1 text" in output and "Page 2 image 1 text" in output


def test_text_only_extraction_does_not_enable_vision(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path, "CMYK")
    def unexpected(*args, **kwargs):
        pytest.fail("Text-only extraction must not call vision")
    monkeypatch.setattr(dp, "analyze_image_with_vl", unexpected)
    assert "no readable content" in dp.extract_local_document(str(pdf), owner="alice")


def test_vision_failure_still_removes_converted_png(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path, "CMYK")
    paths = []
    def unavailable(path, owner=None):
        paths.append(Path(path))
        with Image.open(path) as image:
            assert image.mode == "RGB" and image.format == "PNG"
        raise RuntimeError("Synthetic unavailable vision endpoint")
    monkeypatch.setattr(dp, "analyze_image_with_vl", unavailable)
    assert "no readable content" in dp._process_pdf(str(pdf), owner="alice")
    assert len(paths) == 1 and not paths[0].exists()
