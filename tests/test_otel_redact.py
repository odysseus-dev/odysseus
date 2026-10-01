# tests/test_otel_redact.py
from services.observability.redact import redact_span_attributes


def test_redact_drops_authorization_and_keeps_model():
    out = redact_span_attributes({
        "http.request.header.authorization": "Bearer sk-live",
        "gen_ai.request.model": "openai/cx/gpt-5.5",
        "api_key": "sk-live",
        "gen_ai.input.messages": [{"role": "user", "content": "Hello"}],
    })
    assert "authorization" not in str(out).lower()
    assert "sk-live" not in str(out)
    assert out["gen_ai.request.model"] == "openai/cx/gpt-5.5"
    assert "gen_ai.input.messages" not in out


def test_redact_drops_enc_prefix_values():
    out = redact_span_attributes({"note": "enc:abc", "http.status_code": 401})
    assert "enc:" not in str(out.values())
    assert out["http.status_code"] == 401
