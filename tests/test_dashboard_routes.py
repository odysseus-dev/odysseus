"""Tests for the admin usage metrics dashboard API."""

import pytest
from routes.dashboard_routes import (
    is_local_endpoint,
    _lookup_pricing,
    _compute_cost,
    _date_key,
    MODEL_PRICING,
)
from datetime import datetime


# ------------------------------------------------------------------
# is_local_endpoint
# ------------------------------------------------------------------

class TestIsLocalEndpoint:
    def test_empty_url_is_local(self):
        assert is_local_endpoint("") is True
        assert is_local_endpoint(None) is True

    def test_localhost(self):
        assert is_local_endpoint("http://localhost:8080/v1") is True

    def test_loopback_ip(self):
        assert is_local_endpoint("http://127.0.0.1:11434") is True
        assert is_local_endpoint("http://127.0.1.1:8000") is True

    def test_private_ranges(self):
        assert is_local_endpoint("http://10.0.0.5:8080") is True
        assert is_local_endpoint("http://192.168.1.100:1234") is True
        assert is_local_endpoint("http://172.16.0.1:8080") is True
        assert is_local_endpoint("http://172.31.255.255:8080") is True

    def test_docker_internal(self):
        assert is_local_endpoint("http://host.docker.internal:11434") is True

    def test_dotlocal(self):
        assert is_local_endpoint("http://myserver.local:8080") is True

    def test_single_label_hostname(self):
        assert is_local_endpoint("http://ollama:11434") is True
        assert is_local_endpoint("http://vllm:8000/v1") is True

    def test_tailscale_cgnat(self):
        assert is_local_endpoint("http://100.64.0.1:8080") is True
        assert is_local_endpoint("http://100.100.50.1:8080") is True
        assert is_local_endpoint("http://100.127.255.255:8080") is True

    def test_tailscale_non_cgnat(self):
        assert is_local_endpoint("http://100.128.0.1:8080") is False
        assert is_local_endpoint("http://100.63.0.1:8080") is False

    def test_public_api(self):
        assert is_local_endpoint("https://api.openai.com/v1") is False
        assert is_local_endpoint("https://api.anthropic.com") is False
        assert is_local_endpoint("https://generativelanguage.googleapis.com") is False

    def test_zero_addr(self):
        assert is_local_endpoint("http://0.0.0.0:8080") is True

    def test_invalid_url(self):
        assert is_local_endpoint("not-a-url") is True


# ------------------------------------------------------------------
# _lookup_pricing
# ------------------------------------------------------------------

class TestLookupPricing:
    def test_exact_match(self):
        p = _lookup_pricing("gpt-4o")
        assert p["input"] == 2.50
        assert p["output"] == 10.00

    def test_substring_match(self):
        p = _lookup_pricing("anthropic/claude-3-5-sonnet-20241022")
        assert p["input"] == 3.00

    def test_unknown_model_uses_fallback(self):
        p = _lookup_pricing("totally-unknown-model-xyz")
        assert p["input"] == 1.00
        assert p["output"] == 4.00

    def test_empty_model(self):
        p = _lookup_pricing("")
        assert p["input"] == 1.00

    def test_none_model(self):
        p = _lookup_pricing(None)
        assert p["input"] == 1.00


# ------------------------------------------------------------------
# _compute_cost
# ------------------------------------------------------------------

class TestComputeCost:
    def test_known_model(self):
        # gpt-4o: $2.50 / 1M input, $10.00 / 1M output
        cost = _compute_cost("gpt-4o", 1_000_000, 500_000)
        assert abs(cost - (2.50 + 5.00)) < 0.001

    def test_zero_tokens(self):
        assert _compute_cost("gpt-4o", 0, 0) == 0.0

    def test_small_usage(self):
        cost = _compute_cost("gpt-4o", 1000, 500)
        expected = (1000 * 2.50 + 500 * 10.00) / 1_000_000
        assert abs(cost - expected) < 0.0001


# ------------------------------------------------------------------
# _date_key
# ------------------------------------------------------------------

class TestDateKey:
    def test_datetime_object(self):
        dt = datetime(2025, 3, 15, 10, 30)
        assert _date_key(dt) == "2025-03-15"

    def test_string(self):
        assert _date_key("2025-06-09T12:00:00") == "2025-06-09"

    def test_none(self):
        assert _date_key(None) == "unknown"


# ------------------------------------------------------------------
# MODEL_PRICING sanity
# ------------------------------------------------------------------

def test_pricing_table_has_entries():
    assert len(MODEL_PRICING) > 20

def test_pricing_values_positive():
    for model, p in MODEL_PRICING.items():
        assert p["input"] > 0, f"{model} input pricing should be positive"
        assert p["output"] > 0, f"{model} output pricing should be positive"
