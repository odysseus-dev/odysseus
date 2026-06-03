"""Tests for upload id validation (src/upload_handler.py)."""
import uuid

from src.upload_handler import is_valid_upload_id


def test_extensionless_id_is_valid():
    # save_upload builds `{uuid.hex}{ext}`; a file with no extension yields a
    # bare 32-hex id, which used to fail validation and become unresolvable.
    assert is_valid_upload_id(uuid.uuid4().hex) is True


def test_id_with_extension_still_valid():
    assert is_valid_upload_id(uuid.uuid4().hex + ".png") is True


def test_invalid_ids_rejected():
    assert is_valid_upload_id("not-an-id") is False
    assert is_valid_upload_id(uuid.uuid4().hex + ".") is False
    assert is_valid_upload_id("") is False
    assert is_valid_upload_id(uuid.uuid4().hex + ".tar.gz") is False
