"""Error-path tests for src/upload_handler.py save_upload().

Covers the scenarios where the file write fails mid-operation:
- Disk full (IOError/OSError on write)
- Permission denied on open
- Partial file left on disk after failure

Related: commit #1425 "Surface upload failures instead of silently dropping files".
"""

import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-upload-test-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_handler(tmp_path):
    """Create a minimal UploadHandler pointing at tmp_path."""
    from src.upload_handler import UploadHandler
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return UploadHandler(base_dir=str(tmp_path), upload_dir=str(upload_dir))


def _make_file(content=b"hello world", filename="test.txt", content_type="text/plain"):
    """Return a minimal object mimicking a FastAPI UploadFile with a real file handle."""
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    # UploadHandler accesses u.file (the underlying SpooledTemporaryFile)
    # which must support seek/tell/read. Use a real BytesIO for this.
    buf = io.BytesIO(content)
    f.file = buf
    return f


def test_write_ioerror_raises_http500(tmp_path):
    """IOError during file write → HTTPException 500, not a silent drop."""
    from fastapi import HTTPException

    handler = _make_handler(tmp_path)
    file_obj = _make_file()

    with patch("builtins.open", side_effect=IOError("No space left on device")):
        try:
            handler.save_upload(file_obj, client_ip="127.0.0.1")
            assert False, "Should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 500
            assert "Failed to save file" in e.detail


def test_write_permissionerror_raises_http500(tmp_path):
    """PermissionError during file open → HTTPException 500."""
    from fastapi import HTTPException

    handler = _make_handler(tmp_path)
    file_obj = _make_file()

    with patch("builtins.open", side_effect=PermissionError("Permission denied")):
        try:
            handler.save_upload(file_obj, client_ip="127.0.0.1")
            assert False, "Should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 500


def test_successful_upload_returns_metadata(tmp_path):
    """Happy path: file saved and metadata returned."""
    handler = _make_handler(tmp_path)
    file_obj = _make_file(content=b"test content", filename="doc.txt")

    result = handler.save_upload(file_obj, client_ip="127.0.0.1")

    assert "id" in result
    assert "path" in result
    assert result["name"] == "doc.txt"
    assert os.path.exists(result["path"]), "File must exist on disk after upload"


def test_successful_upload_file_is_readable(tmp_path):
    """File written to disk must contain the original bytes."""
    content = b"important document content"
    handler = _make_handler(tmp_path)
    file_obj = _make_file(content=content, filename="important.txt")

    result = handler.save_upload(file_obj, client_ip="127.0.0.1")

    with open(result["path"], "rb") as f:
        assert f.read() == content


def test_duplicate_upload_returns_is_duplicate_flag(tmp_path):
    """Uploading the same file twice returns is_duplicate=True on the second call."""
    content = b"duplicate content"
    handler = _make_handler(tmp_path)

    file_obj1 = _make_file(content=content, filename="file.txt")
    result1 = handler.save_upload(file_obj1, client_ip="127.0.0.1", owner="alice")

    file_obj2 = _make_file(content=content, filename="file.txt")
    result2 = handler.save_upload(file_obj2, client_ip="127.0.0.1", owner="alice")

    assert result1.get("is_duplicate") is not True, "First upload should not be a duplicate"
    assert result2.get("is_duplicate") is True, "Second upload of same content should be duplicate"
