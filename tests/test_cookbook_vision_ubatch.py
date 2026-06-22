"""Vision serve commands must raise ubatch-size to at least image-max-tokens.

llama-server defaults --ubatch-size to 512 while vision uses --image-max-tokens
1024. mtmd image decode asserts n_ubatch >= n_tokens for non-causal attention,
which aborts the server mid-request (502 / incomplete chunked read).
"""
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/cookbook.js"


def test_vision_serve_bumps_ubatch_to_image_max_tokens():
    text = SRC.read_text(encoding="utf-8")
    assert "_visionImgMaxTok = 1024" in text
    assert "n_ubatch >= image token count" in text
    assert "if (!u || u < _visionImgMaxTok) _ubatchOut = String(_visionImgMaxTok)" in text
