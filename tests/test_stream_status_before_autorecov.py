"""Verify that the catch block in chat.js probes stream_status before
triggering auto-recovery, so a cleanly-finished stream is never nudged
with a spurious "please continue" handshake (fixes #3136)."""

from pathlib import Path


_CHAT_JS = Path("static/js/chat.js")


def _catch_block_src() -> str:
    source = _CHAT_JS.read_text(encoding="utf-8")
    # Locate the outer try/catch that wraps the SSE reader loop
    try_start = source.index("    try {\n      // Re-enable auto-scroll")
    catch_start = source.index("    } catch (err) {", try_start)
    # Find the end of the catch block (the matching closing brace)
    # We just need enough of the catch body to verify the stream_status probe.
    catch_end = source.index("\n    } finally {", catch_start)
    return source[catch_start:catch_end]


def test_stream_status_probed_before_auto_recover():
    """The catch block must fetch stream_status BEFORE calling _tryAutoRecover.

    Without this probe, a connection drop that occurs AFTER [DONE] is emitted
    server-side causes the frontend to send a "please continue" handshake even
    though the response is already saved — effectively discarding the correct
    reply and requesting a duplicate (#3136).
    """
    catch_src = _catch_block_src()

    # The probe must be present
    assert "api/chat/stream_status/" in catch_src, (
        "stream_status probe missing from catch block"
    )

    # Must check specifically for 404 (not just !r.ok) to avoid reloading on
    # auth errors (401/403) or server errors (5xx).
    assert "_stRes.status === 404" in catch_src, (
        "catch block must check for HTTP 404 specifically, not just !res.ok"
    )

    # The probe must appear before _tryAutoRecover in the source
    probe_pos = catch_src.index("api/chat/stream_status/")
    recover_pos = catch_src.index("_tryAutoRecover")
    assert probe_pos < recover_pos, (
        "stream_status probe must appear before _tryAutoRecover in the catch block"
    )


def test_server_finished_path_calls_select_session():
    """When the probe confirms the stream is done, selectSession must be called
    (not _tryAutoRecover) so the user sees the saved response."""
    catch_src = _catch_block_src()

    # Locate the _serverFinished branch
    finished_idx = catch_src.index("_serverFinished")
    select_idx = catch_src.index("selectSession", finished_idx)
    recover_idx = catch_src.index("_tryAutoRecover", finished_idx)

    # selectSession (reload) should appear before _tryAutoRecover in the
    # _serverFinished=true branch.
    assert select_idx < recover_idx, (
        "selectSession must be called in the _serverFinished=true branch, "
        "before _tryAutoRecover in the else branch"
    )
