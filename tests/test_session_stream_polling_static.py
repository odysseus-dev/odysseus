from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_missing_stream_checks_are_coalesced_and_negatively_cached():
    source = (ROOT / "static/js/sessions.js").read_text(encoding="utf-8")

    assert "const _serverStreamChecksInFlight = new Set();" in source
    assert "const _serverStreamAbsentUntil = new Map();" in source
    assert "_serverStreamChecksInFlight.has(sessionId)" in source
    assert "res.status === 404" in source
    assert "Date.now() + SERVER_STREAM_ABSENT_TTL_MS" in source
    assert "_serverStreamChecksInFlight.delete(sessionId);" in source


def test_starting_a_stream_invalidates_the_negative_cache():
    source = (ROOT / "static/js/sessions.js").read_text(encoding="utf-8")
    start = source.index("export function markStreaming(sessionId)")
    body = source[start : start + 300]

    assert "_serverStreamAbsentUntil.delete(sessionId);" in body
