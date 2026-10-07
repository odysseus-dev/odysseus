from types import SimpleNamespace

from src import document_processor
from src.upload_handler import UploadHandler


def test_legacy_word_reader_uses_bounded_strings_fallback(monkeypatch, tmp_path):
    path = tmp_path / "estimate.doc"
    path.write_bytes(b"old binary Word data")

    monkeypatch.setattr(
        document_processor.shutil,
        "which",
        lambda name: "/usr/bin/strings" if name == "strings" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output = "APOL estimate\nOcean freight AUD 2.48/copy\n" if "-e" not in command else ""
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(document_processor.subprocess, "run", fake_run)

    result = document_processor.extract_local_document(str(path))

    assert "Legacy Word content" in result
    assert "Ocean freight AUD 2.48/copy" in result
    assert calls[0][0] == ["strings", "-n", "4", str(path)]
    assert calls[0][1]["timeout"] == 20
    assert calls[0][1]["check"] is False


def test_legacy_word_reader_prefers_antiword(monkeypatch, tmp_path):
    path = tmp_path / "estimate.doc"
    path.write_bytes(b"old binary Word data")
    monkeypatch.setattr(
        document_processor.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"antiword", "strings"} else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="Converted estimate", returncode=0)

    monkeypatch.setattr(document_processor.subprocess, "run", fake_run)

    result = document_processor.extract_local_document(str(path))

    assert "extracted with antiword" in result
    assert calls == [["antiword", str(path)]]


def test_upload_handler_recognizes_legacy_word_documents():
    handler = UploadHandler.__new__(UploadHandler)

    assert handler.is_document_file("estimate.doc") is True
    assert handler.is_document_file("upload", "application/msword") is True
