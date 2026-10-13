from src.agent_loop import (
    _uploaded_file_read_only_turn,
    _uploaded_files_context_message,
)


def _upload(name: str, mime: str = "application/octet-stream") -> dict:
    return {
        "id": "upload-1",
        "name": name,
        "mime": mime,
        "path": f"/tmp/odysseus-uploads/{name}",
    }


def test_uploaded_documents_use_read_file_for_read_only_requests():
    for name, prompt in (
        ("report.docx", "Summarize this document"),
        ("old-report.doc", "Summarize this document"),
        ("contract.pdf", "What is in this file?"),
        ("notes.txt", "Read this"),
        ("data.xlsx", "Inspect the contents"),
        ("letter.docx", "Translate this to Swedish"),
        ("draft.pdf", "Thoughts?"),
    ):
        assert _uploaded_file_read_only_turn([_upload(name)], prompt) is True


def test_uploaded_document_mutations_keep_editing_tools_available():
    assert _uploaded_file_read_only_turn(
        [_upload("report.docx")],
        "Edit this document and save the result",
    ) is False


def test_media_and_unresolved_uploads_do_not_route_to_read_file():
    assert _uploaded_file_read_only_turn(
        [_upload("photo.png", "image/png")],
        "Explain this image",
    ) is False
    unresolved = _upload("report.docx")
    unresolved["path"] = None
    assert _uploaded_file_read_only_turn([unresolved], "Read this") is False


def test_user_can_explicitly_skip_an_uploaded_document():
    assert _uploaded_file_read_only_turn(
        [_upload("report.docx")],
        "Ignore this attachment and answer generally",
    ) is False


def test_uploaded_file_context_requires_native_document_reader():
    message = _uploaded_files_context_message([_upload("report.docx")])

    assert message is not None
    content = message["content"]
    assert "call `read_file`" in content
    assert "Do not use bash" in content
    assert "DOCX" in content
    assert "DOC/DOCX" in content
