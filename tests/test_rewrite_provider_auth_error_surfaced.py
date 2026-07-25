"""Regression guard for issue #5738.

When a quick rewrite action (e.g. "Rewrite shorter") triggers a provider
auth failure (HTTP 401), stream_llm emits an SSE error event whose data
payload uses a "text" field rather than "error":

    event: error
    data: {"status": 401, "text": "...", "raw": "..."}

The rewriteWith() function in chat.js previously only checked `data.error`,
so the auth-failure payload was silently ignored, `newText` stayed empty,
and the UI showed the generic "model returned no rewritten text" message
instead of the actual provider error.

The fix must:
1. Track `event: error` SSE lines (not skip them entirely).
2. Check `data.text` alongside `data.error` when deciding whether the
   chunk represents a provider-side failure.
"""
import re
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parent.parent / "static/js/chat.js"


def _rewrite_with_body() -> str:
    """Return the source of the rewriteWith export function."""
    text = CHAT_JS.read_text(encoding="utf-8")
    start = text.index("export async function rewriteWith(")
    rest = text[start:]
    # Stop at the next top-level export/function so we only look at rewriteWith.
    m = re.search(r"\n(export |function )", rest[1:])
    return rest[: m.start() + 1] if m else rest


def test_rewrite_tracks_event_error_sse_type():
    """rewriteWith must parse 'event: error' lines, not skip them."""
    body = _rewrite_with_body()
    assert re.search(
        r"""line\.startsWith\s*\(\s*['"]event:\s*['"]\s*\)""", body
    ), (
        "rewriteWith must handle 'event: ' lines so the SSE error type can be "
        "tracked; previously these were skipped entirely"
    )


def test_rewrite_error_check_includes_data_text():
    """rewriteWith error detection must cover data.text, not only data.error.

    Provider HTTP errors (e.g. 401 auth failures from ChatGPT Subscription)
    arrive as {"status": N, "text": "...", "raw": "..."} -- no "error" key.
    Without checking data.text the failure is silently swallowed.
    """
    body = _rewrite_with_body()
    # The condition that throws must reference data.text.
    assert "data.text" in body, (
        "rewriteWith error condition must check data.text to catch provider "
        "HTTP errors whose payload uses 'text' instead of 'error'"
    )


def test_rewrite_error_message_prefers_human_readable_text():
    """The thrown Error must use data.error || data.text as the message."""
    body = _rewrite_with_body()
    # Allow either order; both are acceptable.
    assert re.search(r"data\.error\s*\|\|\s*data\.text|data\.text\s*\|\|\s*data\.error", body), (
        "rewriteWith must surface data.error || data.text as the error message "
        "so the user sees the provider's explanation rather than a generic fallback"
    )
