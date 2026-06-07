"""Sender-signature learning must skip role addresses like support@/info@.

The skip-list compares against the email local-part (before "@"), but the
entries were written "support@", "info@", "admin@" — which can never equal or
prefix a local-part of "support"/"info"/"admin", so those role senders were
NOT skipped and the LLM wasted work learning signatures from them. The entries
must omit the "@".
"""
from src.builtin_actions import _SIG_SKIP_PREFIXES


def _skipped(addr):
    local = addr.split("@", 1)[0]
    return any(local == p or local.startswith(p) for p in _SIG_SKIP_PREFIXES)


def test_role_addresses_are_skipped():
    assert _skipped("support@vendor.com")
    assert _skipped("info@company.com")
    assert _skipped("admin@example.org")


def test_noreply_style_still_skipped():
    assert _skipped("noreply@x.com")
    assert _skipped("mailer-daemon@x.com")
    assert _skipped("newsletter@x.com")


def test_real_person_is_not_skipped():
    assert not _skipped("john.smith@x.com")
    assert not _skipped("alice@x.com")


def test_no_skip_entry_contains_at():
    assert all("@" not in p for p in _SIG_SKIP_PREFIXES)
