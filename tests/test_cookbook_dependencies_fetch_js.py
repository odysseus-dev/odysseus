"""Source-level regressions for the Cookbook dependency-list request lifecycle.

The Cookbook bundle imports browser-only modules, so these focused assertions
guard the request/error contract without pretending to run it in a DOM-less
Python test process. JavaScript syntax is checked separately with Node.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COOKBOOK_JS = ROOT / "static" / "js" / "cookbook.js"


def _dependency_fetch_source() -> str:
    source = COOKBOOK_JS.read_text(encoding="utf-8")
    start = source.index("// ── Dependencies ──")
    end = source.index("// ── Tab wiring ──", start)
    return source[start:end]


def test_dependency_fetch_has_timeout_abort_and_latest_request_guard():
    source = _dependency_fetch_source()

    assert "const _DEPENDENCY_FETCH_TIMEOUT_MS = 60000;" in source
    assert "_dependencyFetchController?.abort();" in source
    assert "signal: controller.signal" in source
    assert "controller.abort();" in source
    assert source.count("requestId !== _dependencyFetchRequestId") >= 3


def test_dependency_fetch_rejects_http_and_malformed_json_responses():
    source = _dependency_fetch_source()

    assert "if (!resp.ok)" in source
    assert "data = await resp.json();" in source
    assert "!Array.isArray(data.packages)" in source
    assert "data.packages.every(pkg =>" in source


def test_dependency_fetch_error_is_safe_actionable_and_retryable():
    source = _dependency_fetch_source()

    assert 'class="memory-toolbar-btn cookbook-deps-retry"' in source
    assert "_fetchDependencies();" in source
    assert "timedOut ? 'timeout' : failureKind" in source
    assert "Error loading packages: ${esc(err.message)}" not in source
    assert "${err.message}" not in source
