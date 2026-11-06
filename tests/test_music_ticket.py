import time

import pytest

from src import music_ticket as mt


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))


def test_valid_ticket_for_its_scope_only():
    t = mt.issue_ticket("owner")["ticket"]
    assert mt.verify_ticket(t, mt.SCOPE_STREAM) == "owner"
    assert mt.verify_ticket(t, mt.SCOPE_EVENTS) == "owner"
    assert mt.verify_ticket(t, "cinema:stream") is None


def test_single_scope_ticket_rejects_other_scope():
    t = mt.issue_ticket("owner", scopes=[mt.SCOPE_EVENTS])["ticket"]
    assert mt.verify_ticket(t, mt.SCOPE_EVENTS) == "owner"
    assert mt.verify_ticket(t, mt.SCOPE_STREAM) is None


def test_tampered_or_expired_rejected(monkeypatch):
    t = mt.issue_ticket("owner")["ticket"]
    payload, sig = t.rsplit(".", 1)
    assert mt.verify_ticket(payload + "." + sig[:-2] + "AA", mt.SCOPE_STREAM) is None
    assert mt.verify_ticket(None, mt.SCOPE_STREAM) is None
    expired = mt.issue_ticket("owner", ttl=1)["ticket"]
    real_now = time.time()
    monkeypatch.setattr(mt.time, "time", lambda: real_now + 5)
    assert mt.verify_ticket(expired, mt.SCOPE_STREAM) is None
