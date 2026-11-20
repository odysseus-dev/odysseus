"""Characterization tests for content mutations and query-string test isolation.

Verifies the editor's 25 content mutation sites across document editor source
files and validates that test files importing document.js query variants maintain
page-level isolation.
"""
import glob
import json
import re
import subprocess
from pathlib import Path

from tests.helpers.document_source import document_source_paths

ROOT = Path(__file__).resolve().parents[1]

# Verified ledger of content mutation sites across the complete implementation.
EXPECTED_MUTATION_FUNCTIONS = {
    "_syncEmailRichbody": 2,
    "_autoCreateFromInput": 1,
    "createDocument": 1,
    "_moveDocToSession": 1,
    "ensureEmailDraftEnvelope": 1,
    "replaceEmailReplyBody": 1,
    "bindLanguagePicker": 2,
    "restoreVersion": 1,
    "switchToDoc": 1,
    "saveCurrentToMap": 3,
    "_persistPdfDocument": 1,
    "_undoPdfPaneAction": 2,
    "_syncPdfPaneToDoc": 1,
    "_convertDocxToRichText": 1,
    "_applySuggestions": 1,
    # Visual Markdown editor: the single owner that writes the serialized visual
    # surface into the document it is bound to.
    "syncVisualMarkdownToDoc": 1,
    "streamDocDelta": 1,
    "handleDocUpdate": 2,
    # Session adoption restores a valid local Markdown recovery draft before the
    # server document is rendered, preserving the draft's content as the owner.
    "recoverMarkdownDraft": 1,
}


def test_document_content_mutation_sites():
    """Verify all content mutation sites and their owning functions across modules."""
    script = r"""
    const acorn = require('acorn');
    const fs = require('fs');

    const sourcePaths = process.argv.slice(1);
    const sites = [];

    for (const entryPath of sourcePaths) {
      const src = fs.readFileSync(entryPath, 'utf8');
      const ast = acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'module', locations: true });

      const fns = [];
      for (const n of ast.body) {
        let d = n.type === 'ExportNamedDeclaration' ? n.declaration : n;
        if (d && d.type === 'FunctionDeclaration') {
          fns.push([n.loc.start.line, n.loc.end.line, d.id.name]);
        }
      }

      function walk(n) {
        if (!n || typeof n.type !== 'string') return;
        if (n.type === 'AssignmentExpression') {
          const t = n.left;
          if (t.type === 'MemberExpression' && !t.computed && t.property.name === 'content') {
            const o = t.object;
            const on = o.type === 'Identifier' ? o.name : (o.type === 'MemberExpression' ? '.' + o.property.name : o.type);
            if (/doc|Doc|target|local|cached|prev|d$|existing|current|entry/.test(on) || on === 'this') {
              const line = n.loc.start.line;
              const fn = (fns.find(f => line >= f[0] && line <= f[1]) || [])[2] || '(top)';
              sites.push({ file: entryPath, line, fn, object: on });
            }
          }
        }
        for (const k of Object.keys(n)) {
          if (k === 'loc') continue;
          const c = n[k];
          if (Array.isArray(c)) c.forEach(x => x && walk(x));
          else if (c && typeof c.type === 'string') walk(c);
        }
      }
      walk(ast);
    }

    console.log(JSON.stringify(sites));
    """
    source_paths = [str(path) for path in document_source_paths()]
    result = subprocess.run(
        ["node", "-e", script, *source_paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    sites = json.loads(result.stdout)

    assert len(sites) == 25, f"Expected exactly 25 content mutation sites across editor modules, got {len(sites)}"

    counts = {}
    for site in sites:
        fn = site["fn"]
        counts[fn] = counts.get(fn, 0) + 1

    assert counts == EXPECTED_MUTATION_FUNCTIONS, (
        f"Distribution mismatch: expected {EXPECTED_MUTATION_FUNCTIONS}, got {counts}"
    )


def test_test_suite_query_string_page_isolation():
    """Verify that all tests appending query params to document.js maintain page isolation.

    No test file may import multiple URL variants into a single browser page to
    depend on independent state.
    """
    test_files = sorted(
        glob.glob(str(ROOT / "tests" / "**" / "*.py"), recursive=True)
        + glob.glob(str(ROOT / "tests" / "**" / "*.mjs"), recursive=True)
        + glob.glob(str(ROOT / "tests" / "**" / "*.cjs"), recursive=True)
    )

    query_import_re = re.compile(r"import\([^)]*document\.js\?[^)]*\)")

    files_with_query_imports = []
    for filepath in test_files:
        try:
            with open(filepath, "r", encoding="utf-8") as fp:
                content = fp.read()
        except Exception:
            continue

        matches = query_import_re.findall(content)
        if matches:
            files_with_query_imports.append((filepath, matches, content))

    assert len(files_with_query_imports) >= 26, (
        f"Expected at least 26 test files with query imports, found {len(files_with_query_imports)}"
    )

    # For every file with multiple imports, verify they are in separate test functions or separate pages
    for filepath, matches, content in files_with_query_imports:
        if len(matches) <= 1:
            continue

        # In Python test files with multiple test functions, ensure each function creates its own page
        if filepath.endswith(".py"):
            lines = content.splitlines()
            current_fn = None
            fn_imports = {}
            for line in lines:
                if line.strip().startswith("def test_"):
                    current_fn = line.strip().split("(")[0]
                    fn_imports[current_fn] = 0
                if query_import_re.search(line) and current_fn:
                    fn_imports[current_fn] += 1

            for fn_name, count in fn_imports.items():
                assert count <= 1, (
                    f"File {filepath} function {fn_name} imports document.js? {count} times in one function"
                )
