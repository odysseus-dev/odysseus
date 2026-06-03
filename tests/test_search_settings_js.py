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


def _load_count_helper_source() -> str:
    source = _SETTINGS_JS.read_text(encoding="utf-8")
    match = re.search(
        r"export function resolveSearchResultCount\([^)]*\) \{[\s\S]*?\n\}",
        source,
    )
    assert match, "resolveSearchResultCount helper is missing"
    return match.group(0).replace("export function", "function", 1)


def test_custom_search_result_count_helper_preserves_and_saves_custom_value(node_available):
    helper_source = _load_count_helper_source()
    script = textwrap.dedent(
        f"""
        import assert from 'node:assert/strict';

        {helper_source}

        assert.equal(resolveSearchResultCount('custom', '15', 5), 15);
        assert.equal(resolveSearchResultCount('custom', '', 20), 20);
        assert.equal(resolveSearchResultCount('custom', '200', 10), 10);
        assert.equal(resolveSearchResultCount('10', '', 5), 10);
        assert.equal(resolveSearchResultCount('bad', '', 7), 7);
        """
    )
    subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_custom_search_result_input_triggers_save():
    source = _SETTINGS_JS.read_text(encoding="utf-8")

    assert "countCustomInput.addEventListener('change', saveSearch);" in source
