"""Behavioral and integration coverage for document editor statistics."""

import json
import subprocess
from pathlib import Path

from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def _run_stats(expression: str):
    script = f"""
      import {{ getDocumentStats, countDocumentWords, countDocumentCharacters }}
        from './static/js/documentStats.js';
      console.log(JSON.stringify({expression}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_stats_count_words_lines_characters_and_reading_time():
    assert _run_stats("getDocumentStats('Hello, world!\\nSecond line.')") == {
        "words": 4,
        "characters": 26,
        "charactersNoSpaces": 23,
        "lines": 2,
        "readingMinutes": 1,
    }


def test_stats_are_unicode_word_and_grapheme_aware():
    result = _run_stats("({ japanese: countDocumentWords('これはテストです'), emoji: countDocumentCharacters('A 👨‍👩‍👧‍👦') })")
    assert result["japanese"] > 1
    assert result["emoji"] == 3


def test_stats_footer_is_selection_aware_in_both_editors():
    assert "_normalizeRichStatsText(selection.toString()), selected: true" in DOC_JS
    assert ".replace(/\\n{2,}/gu, '\\n')" in DOC_JS
    assert "textarea.value.slice(start, end), selected: true" in DOC_JS
    assert "source.selected ? 'Selection' : 'Document'" in DOC_JS
    assert "ta.addEventListener(eventName, _scheduleDocumentStats);" in DOC_JS
    assert "_scheduleDocumentStats();" in DOC_JS


def test_stats_popover_is_compact_on_mobile():
    assert 'id="doc-stats-popover"' in DOC_JS
    assert ".doc-stats-wrap" in STYLE
    assert "#doc-actions-footer .doc-stats-unit { display: none; }" in STYLE
    assert ".doc-stats-popover[hidden] { display: none; }" in STYLE
