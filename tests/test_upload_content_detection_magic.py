"""Regression for #4875: the official Docker image shipped without python-magic
(and without the libmagic system lib), so content-based MIME detection in
src/upload_handler.py was dead and uploads were typed by extension only.

python-magic resolves libmagic at import time and can block/raise when the lib
is absent, so it's installed in the Docker image (which always has libmagic1)
rather than in the shared requirements.txt. These tests pin:
  1. the Dockerfile installs both libmagic1 (apt) and python-magic (pip);
  2. when libmagic is actually present, detect_content_type sniffs the MIME
     from the bytes and overrides a misleading/missing extension.
"""
import io
import os

import pytest

from src.upload_handler import UploadHandler

# 1x1 PNG (header is enough for libmagic to report image/png).
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_dockerfile_installs_libmagic_and_python_magic():
    with open(os.path.join(_REPO_ROOT, "Dockerfile"), encoding="utf-8") as f:
        dockerfile = f.read()
    # The C library python-magic dlopens, installed via apt...
    assert "libmagic1" in dockerfile
    # ...and the wrapper itself, installed via pip in the image.
    assert "python-magic" in dockerfile


def test_content_detection_overrides_misleading_extension(tmp_path):
    handler = UploadHandler(base_dir=str(tmp_path), upload_dir=str(tmp_path))
    if handler.file_detector is None:
        pytest.skip("libmagic/python-magic not installed in this environment")

    # PNG bytes behind a .bin name: extension sniffing can't help, so a correct
    # image/png result proves content-based detection is doing the work.
    detected = handler.detect_content_type(io.BytesIO(_PNG), "payload.bin")
    assert detected == "image/png"
