"""Tests for the RateLimiter — pure in-memory, no server needed."""

import time
import pytest

from src.rate_limiter import RateLimiter


class TestRateLimiterAllow:
    def test_allows_under_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.check("ip1") is True
        assert rl.check("ip1") is True
        assert rl.check("ip1") is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            rl.check("ip1")
        assert rl.check("ip1") is False

    def test_different_keys_independent(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        assert rl.check("ip1") is True
        assert rl.check("ip2") is True
        assert rl.check("ip1") is False
        assert rl.check("ip2") is False


class TestRateLimiterExpiry:
    def test_window_expiry(self):
        rl = RateLimiter(max_requests=1, window_seconds=1)
        assert rl.check("ip1") is True
        assert rl.check("ip1") is False
        time.sleep(1.1)
        assert rl.check("ip1") is True


class TestRateLimiterCleanup:
    def test_cleanup_removes_stale_entries(self):
        rl = RateLimiter(max_requests=1, window_seconds=1)
        rl._cleanup_interval = 0  # Force cleanup on every check
        rl.check("ip1")
        assert "ip1" in rl._log
        time.sleep(1.1)
        rl.check("ip2")  # Triggers cleanup
        assert "ip1" not in rl._log
