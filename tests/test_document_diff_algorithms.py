"""Characterization and regression coverage for document diff algorithms.

Characterizes return shapes, tie-breaking, repeated-line handling, size guards,
downstream consumer contracts, and HTML escaping deduplication across:
- diffLines
- _computeLineDiff
- lineDiff
- _buildDiffSummary
- _buildDiffChunks
- _escHtml / _esc
"""

import json
import subprocess
from tests.helpers.document_source import function_body


def _eval_js(expr: str):
    """Execute a JavaScript expression with extracted diff functions."""
    fn_code = "\n".join([
        function_body("diffLines"),
        function_body("_computeLineDiff"),
        function_body("lineDiff"),
        function_body("_buildDiffSummary"),
        function_body("_buildDiffChunks"),
        function_body("_escHtml"),
        function_body("_esc"),
    ])
    script = f"""
    {fn_code}
    const evaluated = ({expr});
    console.log(JSON.stringify(evaluated));
    """
    res = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(res.stdout)


def test_diff_return_shapes_and_types():
    """Verify return shapes and entry contracts for diff functions."""
    result = _eval_js(
        """({
            compute: _computeLineDiff("alpha\\nbeta", "alpha\\ngamma"),
            line: lineDiff("alpha\\nbeta", "alpha\\ngamma"),
            summary: _buildDiffSummary("alpha\\nbeta", "alpha\\ngamma"),
        })"""
    )

    # _computeLineDiff: [{type: 'equal'|'insert'|'delete', line: string}]
    assert result["compute"] == [
        {"type": "equal", "line": "alpha"},
        {"type": "delete", "line": "beta"},
        {"type": "insert", "line": "gamma"},
    ]

    # lineDiff: [{type: 'same'|'del'|'add', text: string}]
    assert result["line"] == [
        {"type": "same", "text": "alpha"},
        {"type": "del", "text": "beta"},
        {"type": "add", "text": "gamma"},
    ]

    # _buildDiffSummary: formatted HTML string
    assert '<span class="diff-del">beta</span>' in result["summary"]
    assert '<span class="diff-add">gamma</span>' in result["summary"]


def test_diff_tie_breaking_and_replacement_ordering():
    """Characterize tie-breaking on ambiguous matches and replacement order."""
    result = _eval_js(
        """({
            ambiguousCompute: _computeLineDiff("A\\nB", "B\\nA"),
            ambiguousLine: lineDiff("A\\nB", "B\\nA"),
            replacementCompute: _computeLineDiff("X\\nA\\nY", "X\\nB\\nY"),
            replacementLine: lineDiff("X\\nA\\nY", "X\\nB\\nY"),
        })"""
    )

    # In ambiguous matches (LCS length 1), diffLines matches line B
    assert result["ambiguousCompute"] == [
        {"type": "delete", "line": "A"},
        {"type": "equal", "line": "B"},
        {"type": "insert", "line": "A"},
    ]
    assert result["ambiguousLine"] == [
        {"type": "del", "text": "A"},
        {"type": "same", "text": "B"},
        {"type": "add", "text": "A"},
    ]

    # On replacement, deletions precede additions
    assert result["replacementCompute"] == [
        {"type": "equal", "line": "X"},
        {"type": "delete", "line": "A"},
        {"type": "insert", "line": "B"},
        {"type": "equal", "line": "Y"},
    ]
    assert result["replacementLine"] == [
        {"type": "same", "text": "X"},
        {"type": "del", "text": "A"},
        {"type": "add", "text": "B"},
        {"type": "same", "text": "Y"},
    ]


def test_diff_repeated_line_behavior():
    """Characterize behavior when lines are repeated consecutively."""
    result = _eval_js(
        """({
            delRepeated: _computeLineDiff("A\\nA\\nA", "A\\nA"),
            addRepeated: _computeLineDiff("A\\nA", "A\\nA\\nA"),
            interleaved: _computeLineDiff("A\\nB\\nA", "A\\nA"),
        })"""
    )

    assert result["delRepeated"] == [
        {"type": "delete", "line": "A"},
        {"type": "equal", "line": "A"},
        {"type": "equal", "line": "A"},
    ]
    assert result["addRepeated"] == [
        {"type": "insert", "line": "A"},
        {"type": "equal", "line": "A"},
        {"type": "equal", "line": "A"},
    ]
    assert result["interleaved"] == [
        {"type": "equal", "line": "A"},
        {"type": "delete", "line": "B"},
        {"type": "equal", "line": "A"},
    ]


def test_diff_large_input_handling_and_size_guard():
    """Verify that inputs exceeding 500,000 cells are protected against UI freeze."""
    result = _eval_js(
        """({
            lineDiffOverLimit: lineDiff("x\\n".repeat(800), "y\\n".repeat(800)),
            computeDiffOverLimit: _computeLineDiff("x\\n".repeat(800), "y\\n".repeat(800)),
        })"""
    )

    # 801 * 801 = 641,601 cells > 500,000 maxCells
    # lineDiff must return null so streaming animation gracefully skips
    assert result["lineDiffOverLimit"] is None

    # _computeLineDiff must return fallback delete+insert without freezing
    compute_entries = result["computeDiffOverLimit"]
    assert len(compute_entries) == 1602
    assert all(e["type"] == "delete" for e in compute_entries[:801])
    assert all(e["type"] == "insert" for e in compute_entries[801:])


def test_diff_large_document_with_small_change_preserves_prefix_and_suffix():
    """Verify that an 800-line document with a single changed line produces a bounded per-hunk diff."""
    result = _eval_js(
        """(() => {
            const linesOld = Array.from({ length: 800 }, (_, i) => "Line " + i);
            const linesNew = linesOld.slice();
            linesNew[400] = "Modified Line 400";
            const oldText = linesOld.join("\\n");
            const newText = linesNew.join("\\n");

            const raw = diffLines(oldText, newText);
            const entries = _computeLineDiff(oldText, newText);
            const chunks = _buildDiffChunks(entries);
            return {
                rawNotNone: raw !== null,
                totalEntries: entries.length,
                chunkCount: chunks.length,
                chunkStart: chunks[0].startLine,
                oldChunk: chunks[0].oldLines,
                newChunk: chunks[0].newLines,
                prefixEqualCount: entries.slice(0, 400).filter(e => e.type === "equal").length,
                suffixEqualCount: entries.slice(402).filter(e => e.type === "equal").length,
            };
        })()"""
    )

    assert result["rawNotNone"] is True
    assert result["totalEntries"] == 801
    assert result["chunkCount"] == 1
    assert result["chunkStart"] == 400
    assert result["oldChunk"] == ["Line 400"]
    assert result["newChunk"] == ["Modified Line 400"]
    assert result["prefixEqualCount"] == 400
    assert result["suffixEqualCount"] == 399


def test_diff_downstream_consumer_contracts():
    """Verify that downstream consumers correctly process diff outputs."""
    result = _eval_js(
        """(() => {
            const entries = _computeLineDiff("line1\\nline2\\nline3", "line1\\nmodified\\nline3");
            const chunks = _buildDiffChunks(entries);
            const emptySummary = _buildDiffSummary("", "");
            const multiChangeSummary = _buildDiffSummary(
                "one\\ntwo\\nthree\\nfour\\nfive",
                "1\\n2\\n3\\n4\\n5"
            );
            return { chunks, emptySummary, multiChangeSummary };
        })()"""
    )

    # _buildDiffChunks creates a contiguous chunk around changes
    assert len(result["chunks"]) == 1
    chunk = result["chunks"][0]
    assert chunk["id"] == 0
    assert chunk["oldLines"] == ["line2"]
    assert chunk["newLines"] == ["modified"]
    assert chunk["startLine"] == 1
    assert chunk["resolved"] is False
    assert chunk["accepted"] is False

    # _buildDiffSummary handles empty texts
    assert result["emptySummary"] == ""

    # _buildDiffSummary caps output to 2 removed and 2 added plus overflow note
    summary = result["multiChangeSummary"]
    assert '<span class="diff-del">' in summary
    assert '<span class="diff-add">' in summary
    assert "+6 more changes" in summary


def test_html_escaping_unification():
    """Verify that _esc and _escHtml are identical, safe, and handle edge cases."""
    result = _eval_js(
        """({
            escSpecial: _esc('& < > "'),
            escHtmlSpecial: _escHtml('& < > "'),
            escNull: _esc(null),
            escHtmlNull: _escHtml(null),
            escUndefined: _esc(undefined),
            escHtmlUndefined: _escHtml(undefined),
            escNumber: _esc(42),
            escHtmlNumber: _escHtml(42),
        })"""
    )

    expected_escaped = "&amp; &lt; &gt; &quot;"
    assert result["escSpecial"] == expected_escaped
    assert result["escHtmlSpecial"] == expected_escaped

    assert result["escNull"] == ""
    assert result["escHtmlNull"] == ""

    assert result["escUndefined"] == ""
    assert result["escHtmlUndefined"] == ""

    assert result["escNumber"] == "42"
    assert result["escHtmlNumber"] == "42"
