from pathlib import Path


def test_session_compact_is_exempt_from_the_request_hard_timeout():
    source = Path("app.py").read_text()
    start = source.index("_TIMEOUT_EXEMPT_SESSION_SUFFIXES =")
    line = source[start:source.index("\n", start)]

    assert '"/compact"' in line
    assert 'path.startswith("/api/session/") and path.endswith(_TIMEOUT_EXEMPT_SESSION_SUFFIXES)' in source


def test_compaction_summary_timeout_is_configurable():
    source = Path("src/context_compactor.py").read_text()

    assert 'SUMMARY_TIMEOUT_SECONDS = float(os.getenv("COMPACT_SUMMARY_TIMEOUT", "30"))' in source
    assert "timeout=SUMMARY_TIMEOUT_SECONDS," in source
    assert "timeout=30," not in source
