"""Regression guard: Serve must be able to list Docker-local HF cache models.

Downloads done from Docker land in the Local container cache. If the Serve cache
server selector excludes Local, freshly downloaded models can be complete on disk
but invisible in the Serve tab.
"""
from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "static/js/cookbook.js"


def test_serve_cache_selector_includes_local_server():
    text = SRC.read_text(encoding="utf-8")
    assert (
        'id="hwfit-cache-server" style="height:24px;">\' + _buildServerOpts(false)'
        in text
    )


def test_download_selector_still_excludes_local_server():
    text = SRC.read_text(encoding="utf-8")
    marker = 'id="hwfit-dl-server"'
    idx = text.index(marker)
    assert "_buildServerOpts(true)" in text[idx : idx + 260]
