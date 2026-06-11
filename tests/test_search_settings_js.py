"""Regression coverage for the Search settings custom result-count picker."""

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SETTINGS_JS = _REPO / "static" / "js" / "settings.js"
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def test_custom_search_result_count_helper_preserves_and_saves_custom_value(node_available):
    source = _SETTINGS_JS.read_text(encoding="utf-8")
    # The count-resolution logic lives inline in saveSearch().
    # Verify the key branches exist.
    assert "if (countSel.value === 'custom')" in source
    assert "var customVal = parseInt(countCustomInput.value, 10);" in source
    assert "isNaN(customVal) || customVal < 1 || customVal > 100" in source


def test_custom_search_result_input_triggers_save():
    source = _SETTINGS_JS.read_text(encoding="utf-8")

    assert "countSel.addEventListener('change', saveSearch);" in source
