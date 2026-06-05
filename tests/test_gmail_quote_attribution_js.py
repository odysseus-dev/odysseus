"""Pin _extractQuoteMeta's Gmail attribution parsing (static/js/emailLibrary/signatureFold.js).

Driven through `node --input-type=module` (same approach as test_hex_to_rgb_js.py);
skips when `node` is not installed.

Regression: the Gmail-fallback date pattern allowed only ONE comma before the
4-digit year, but the standard US Gmail attribution
"On Mon, Apr 18, 2026 at 9:31 AM, Jane Doe <jane@example.com> wrote:" carries
TWO (after the weekday and after the day-of-month). The match failed, so the
collapsed "Earlier thread"/"Earlier reply" fold rendered without its
sender/date headline for the most common Gmail reply format.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emailLibrary" / "signatureFold.js"
_HAS_NODE = shutil.which("node") is not None


def _meta(html: str) -> str:
    js = (
        # _esc in the module touches `document` lazily; stub it so the module
        # can be exercised outside a browser.
        "globalThis.document = { createElement() { return {"
        " set textContent(v) { this._t = v; },"
        " get innerHTML() { return this._t || ''; } }; } };"
        f"const {{ _extractQuoteMeta }} = await import('{_HELPER.as_posix()}');"
        f"console.log(JSON.stringify(_extractQuoteMeta({json.dumps(html)})));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_us_gmail_attribution_with_weekday_extracts_sender_and_date():
    meta = _meta("On Mon, Apr 18, 2026 at 9:31 AM, Jane Doe &lt;jane@example.com&gt; wrote:")
    # date is clamped to 28 chars by the helper; sender must be present.
    assert meta.startswith("Jane Doe jane@example.com")
    assert "Mon, Apr 18, 2026" in meta


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_gmail_attribution_without_time_extracts_sender():
    meta = _meta("On Wed, Jan 1, 2025, Jane wrote:")
    assert meta == "Jane · Wed, Jan 1, 2025"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_previously_working_formats_still_match():
    # No weekday (single comma before the year).
    meta = _meta("On Apr 18, 2026 at 9:31 AM, Jane Doe wrote:")
    assert meta.startswith("Jane Doe · Apr 18, 2026")
    # UK/intl day-before-month order.
    meta = _meta("On Mon, 18 Apr 2026 at 09:31, Jane Doe &lt;jane@example.com&gt; wrote:")
    assert meta.startswith("Jane Doe jane@example.com")
