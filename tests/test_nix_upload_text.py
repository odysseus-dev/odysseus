import pytest

import src.document_processor as document_processor
from src.document_processor import _is_text_file, _process_text_file
from src.upload_handler import UploadHandler, is_text_attachment


def test_nix_files_are_treated_as_readable_documents(tmp_path):
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))

    assert handler.is_document_file("configuration.nix")
    assert _is_text_file("configuration.nix")


def test_nix_file_processing_includes_content_in_code_block(tmp_path):
    nix_file = tmp_path / "configuration.nix"
    nix_file.write_text("{ pkgs, ... }:\n{\n  services.openssh.enable = true;\n}\n", encoding="utf-8")

    rendered = _process_text_file(str(nix_file))

    assert "[Type: nix" in rendered
    assert "```nix" in rendered
    assert "services.openssh.enable = true;" in rendered


def _build_user_content(tmp_path, *, stored_name, display_name, mime, body):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    path = upload_dir / stored_name
    path.write_bytes(body)
    handler = UploadHandler(str(tmp_path), str(upload_dir))

    return document_processor.build_user_content(
        "Read this attachment.",
        ["attachment-id"],
        str(upload_dir),
        handler,
        owner="tester",
        resolved_uploads={
            "attachment-id": {
                "path": str(path),
                "name": display_name,
                "mime": mime,
            }
        },
    )


def test_text_mime_without_recognized_extension_reaches_user_content(tmp_path):
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))
    mime = "Text/Markdown; charset=utf-8"

    assert is_text_attachment("release-notes.unknown", mime)
    assert handler.is_document_file("release-notes.unknown", mime)

    content = _build_user_content(
        tmp_path,
        stored_name="0123456789abcdef0123456789abcdef",
        display_name="release-notes.unknown",
        mime=mime,
        body=b"Deployment requires a database backup.\n",
    )

    assert "=== File: release-notes.unknown ===" in content
    assert "Deployment requires a database backup." in content


@pytest.mark.parametrize(
    ("filename", "body"),
    [
        ("include/widget.h", b"#define WIDGET_LIMIT 8\n"),
        ("deploy/config.yaml", b"replicas: 3\n"),
    ],
)
def test_text_extension_with_opaque_mime_reaches_user_content(tmp_path, filename, body):
    display_name = filename.rsplit("/", 1)[-1]

    assert is_text_attachment(filename, "application/octet-stream")

    content = _build_user_content(
        tmp_path,
        stored_name=f"0123456789abcdef0123456789abcdef.{display_name.rsplit('.', 1)[-1]}",
        display_name=display_name,
        mime="application/octet-stream",
        body=body,
    )

    assert f"=== File: {display_name} ===" in content
    assert body.decode().strip() in content


def test_pdf_and_office_files_are_not_classified_as_generic_text(tmp_path):
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))

    assert handler.is_document_file("packet.pdf", "application/pdf")
    assert handler.is_document_file(
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert not is_text_attachment("packet.pdf", "application/pdf")
    assert not is_text_attachment(
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def test_pdf_and_office_files_keep_specialized_processing_paths(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        document_processor,
        "_process_pdf",
        lambda path, owner=None: calls.append(("pdf", path)) or "\n\n[pdf text]",
    )
    monkeypatch.setattr(
        document_processor,
        "_process_office_document",
        lambda path, display_name, **kwargs: calls.append(("office", path)) or "\n\n[office text]",
    )

    pdf_content = _build_user_content(
        tmp_path,
        stored_name="0123456789abcdef0123456789abcdef.pdf",
        display_name="packet.pdf",
        mime="application/octet-stream",
        body=b"%PDF-1.4 fake",
    )
    office_content = _build_user_content(
        tmp_path,
        stored_name="fedcba9876543210fedcba9876543210.docx",
        display_name="report.docx",
        mime="application/octet-stream",
        body=b"fake office file",
    )

    assert [kind for kind, _path in calls] == ["pdf", "office"]
    assert "[pdf text]" in pdf_content
    assert "[office text]" in office_content


def test_office_extension_takes_precedence_over_text_mime(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        document_processor,
        "_process_office_document",
        lambda path, display_name, **kwargs: calls.append(display_name) or "\n\n[office text]",
    )

    content = _build_user_content(
        tmp_path,
        stored_name="0123456789abcdef0123456789abcdef.docx",
        display_name="report.docx",
        mime="text/plain; charset=utf-8",
        body=b"fake office file",
    )

    assert calls == ["report.docx"]
    assert "[office text]" in content


def test_unknown_binary_attachment_is_not_decoded_as_text(tmp_path):
    content = _build_user_content(
        tmp_path,
        stored_name="0123456789abcdef0123456789abcdef.bin",
        display_name="payload.bin",
        mime="application/octet-stream",
        body=b"SECRET_ASCII_INSIDE_BINARY\x00\xff",
    )

    assert "[Attached non-text file]" in content
    assert "SECRET_ASCII_INSIDE_BINARY" not in content
