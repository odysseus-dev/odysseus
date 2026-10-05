"""fetch_webpage_content extracts text from PDFs without pdfminer.six (#6493).

pdfminer.six is in neither requirements file, so on Docker every PDF came back
with "Failed to extract PDF text". pypdf is a hard dependency and is what the
upload path already uses, so the web path falls back to it.
"""
import pytest

from services.search import content as content_mod


def _make_pdf(text: str) -> bytes:
    """Build a one-page PDF whose content stream draws ``text``."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str):
        self.content = content
        self.text = ""
        self.headers = {"Content-Type": content_type}
        self.status_code = 200

    def raise_for_status(self):
        return None


@pytest.fixture
def no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(content_mod, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content_mod, "_cache_result", lambda *a, **k: None)


def _patch_fetch(monkeypatch, content: bytes, content_type="application/pdf"):
    monkeypatch.setattr(
        content_mod,
        "_get_public_url",
        lambda url, headers=None, timeout=5, **kwargs: _FakeResponse(content, content_type),
    )


def test_pdf_text_extracted_without_pdfminer(monkeypatch, no_cache):
    monkeypatch.setattr(content_mod, "pdf_extract_text", None)
    _patch_fetch(monkeypatch, _make_pdf("Attention is all you need"))
    r = content_mod.fetch_webpage_content("https://arxiv.org/pdf/1706.03762")
    assert r["success"] is True, r
    assert "Attention is all you need" in r["content"]
    assert r["error"] == ""


def test_pdf_by_url_suffix_extracted(monkeypatch, no_cache):
    monkeypatch.setattr(content_mod, "pdf_extract_text", None)
    _patch_fetch(monkeypatch, _make_pdf("Suffix match"), content_type="application/octet-stream")
    r = content_mod.fetch_webpage_content("https://example.com/paper.pdf")
    assert r["success"] is True, r
    assert "Suffix match" in r["content"]


def test_pdfminer_used_when_installed(monkeypatch, no_cache):
    monkeypatch.setattr(content_mod, "pdf_extract_text", lambda _buf: "from pdfminer")
    _patch_fetch(monkeypatch, _make_pdf("ignored"))
    r = content_mod.fetch_webpage_content("https://example.com/doc.pdf")
    assert r["content"] == "from pdfminer"


def test_pdfminer_failure_falls_back_to_pypdf(monkeypatch, no_cache):
    def boom(_buf):
        raise ValueError("pdfminer choked")

    monkeypatch.setattr(content_mod, "pdf_extract_text", boom)
    _patch_fetch(monkeypatch, _make_pdf("Fallback text"))
    r = content_mod.fetch_webpage_content("https://example.com/doc.pdf")
    assert r["success"] is True, r
    assert "Fallback text" in r["content"]


def test_unparseable_pdf_reports_failure(monkeypatch, no_cache):
    monkeypatch.setattr(content_mod, "pdf_extract_text", None)
    _patch_fetch(monkeypatch, b"%PDF-1.4 not really a pdf")
    r = content_mod.fetch_webpage_content("https://example.com/broken.pdf")
    assert r["success"] is False
    assert r["error"] == "Failed to extract PDF text"
