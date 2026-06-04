import io

import pytest
from fastapi import HTTPException, UploadFile

from src.chat_helpers import validate_file_upload
from src.document_processor import _is_text_file, _process_text_file


def _upload(name: str, data: bytes = b"content") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


@pytest.mark.parametrize("filename", [
    "component.tsx",
    "view.jsx",
    "styles.css",
    "schema.yml",
    "script.sh",
    "query.sql",
    "main.rs",
    "server.go",
    "Widget.java",
    "native.hpp",
    "pyproject.toml",
    "settings.ini",
])
def test_validate_file_upload_accepts_common_code_files(filename):
    upload = _upload(filename)

    assert validate_file_upload(upload) is upload


def test_validate_file_upload_keeps_rejecting_executables():
    with pytest.raises(HTTPException) as exc:
        validate_file_upload(_upload("run.exe"))

    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "UNSUPPORTED_FILE_TYPE"


@pytest.mark.parametrize("filename", [
    "component.tsx",
    "view.jsx",
    "styles.css",
    "schema.yml",
    "script.sh",
    "query.sql",
    "main.rs",
    "server.go",
    "Widget.java",
    "native.hpp",
    "pyproject.toml",
    "settings.ini",
])
def test_document_processor_treats_common_code_files_as_text(filename):
    assert _is_text_file(filename)


def test_process_text_file_formats_typescript_attachment(tmp_path):
    path = tmp_path / "component.tsx"
    path.write_text("export const value: number = 1;\n", encoding="utf-8")

    rendered = _process_text_file(str(path))

    assert "[Type: typescript" in rendered
    assert "```typescript" in rendered
    assert "export const value" in rendered
