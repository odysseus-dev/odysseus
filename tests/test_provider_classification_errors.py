"""Upstream-error formatting for provider setup (REAL src.llm_core).

Split from `test_provider_classification.py` to keep error-message formatting
separate from provider identification.

  * `_format_upstream_error` — turns a raw upstream HTTP status + body into the
    one-line, provider-aware message the UI shows ("Provider probes" degraded
    reporting in the roadmap).

conftest.py stubs the heavy deps (sqlalchemy, src.database), so importing the
real module is side-effect free.
"""
from src.llm_core import _format_upstream_error


# ── _format_upstream_error ──
# Status + body → one-line provider-aware sentence.

class TestFormatUpstreamError:
    def test_401_rejects_key_with_provider_and_detail(self):
        msg = _format_upstream_error(
            401, '{"error": {"message": "Invalid API key"}}', "https://api.x.ai/v1"
        )
        assert msg.startswith("xAI rejected the API key")
        assert "Invalid API key" in msg
        assert "re-paste the key" in msg

    def test_403_denies_access(self):
        msg = _format_upstream_error(
            403, '{"error": {"message": "Forbidden"}}', "https://api.openai.com/v1"
        )
        assert "OpenAI denied access (403)" in msg
        assert "Forbidden" in msg

    def test_404_points_at_base_url(self):
        msg = _format_upstream_error(404, "", "https://api.groq.com/openai/v1")
        assert msg == "Groq returned 404 — check the base URL and model name."

    def test_429_rate_limited(self):
        msg = _format_upstream_error(
            429, '{"error": {"message": "slow down"}}', "https://api.anthropic.com"
        )
        assert msg.startswith("Anthropic rate-limited the request (429).")
        assert "slow down" in msg

    def test_5xx_reported_as_outage(self):
        msg = _format_upstream_error(503, "", "https://api.deepseek.com")
        assert msg == "DeepSeek is having an outage (HTTP 503)."

    def test_other_status_passthrough(self):
        msg = _format_upstream_error(418, "", "https://api.openai.com/v1")
        assert msg == "OpenAI returned HTTP 418"

    def test_string_error_field(self):
        msg = _format_upstream_error(401, '{"error": "bad key"}', "https://api.openai.com/v1")
        assert "bad key" in msg

    def test_plain_text_body_used_as_detail(self):
        msg = _format_upstream_error(500, "upstream exploded", "https://api.openai.com/v1")
        assert "OpenAI is having an outage (HTTP 500)." in msg
        assert "upstream exploded" in msg

    def test_bytes_body_is_decoded(self):
        msg = _format_upstream_error(
            401, b'{"error": {"message": "nope"}}', "https://api.openai.com/v1"
        )
        assert "nope" in msg

    def test_unknown_url_falls_back_to_generic_label(self):
        msg = _format_upstream_error(401, "", "")
        assert msg.startswith("provider rejected the API key")
