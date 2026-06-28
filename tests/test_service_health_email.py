"""Tests for email_health — probe logic, status classification, sanitization, and bounded timeout."""
import pytest

from src import service_health as sh


def _raise(*_a, **_k):
    raise RuntimeError("connection refused")


def _acct(name, host="imap.example.com"):
    return {"account_id": name, "account_name": name, "imap_host": host,
            "imap_password": "hunter2"}


class _Conn:
    def logout(self):
        pass


def test_email_disabled_without_accounts():
    assert sh.email_health([])["status"] == sh.DISABLED


def test_email_ok_all_connect():
    s = sh.email_health([_acct("a"), _acct("b")], connect=lambda _id: _Conn())
    assert s["status"] == sh.OK


def test_email_degraded_some_fail():
    def connect(account_id):
        if account_id == "bad":
            raise RuntimeError("auth failed")
        return _Conn()

    s = sh.email_health([_acct("good"), _acct("bad")], connect=connect)
    assert s["status"] == sh.DEGRADED


def test_email_down_all_fail():
    s = sh.email_health([_acct("a")], connect=_raise)
    assert s["status"] == sh.DOWN


def test_email_account_without_host_marked_failed():
    s = sh.email_health([_acct("a", host="")], connect=lambda _id: _Conn())
    assert s["status"] == sh.DOWN


def test_email_meta_never_leaks_password():
    s = sh.email_health([_acct("a")], connect=lambda _id: _Conn())
    assert "hunter2" not in repr(s)


def test_email_connect_exception_maps_to_category():
    def boom(account_id):
        raise RuntimeError("login failed for user bob with password hunter2")
    s = sh.email_health([_acct("a")], connect=boom)
    assert s["status"] == sh.DOWN
    assert s["meta"]["accounts"][0]["error"] == "error"
    assert "hunter2" not in repr(s)


def test_email_bounded_marks_slow_as_timeout(monkeypatch):
    import time
    monkeypatch.setattr(sh, "_FANOUT_BUDGET", 1)

    def connect(account_id):
        if account_id == "slow":
            time.sleep(10)
        return _Conn()

    accts = [_acct("fast"), _acct("slow")]
    accts[1]["account_id"] = "slow"
    t0 = time.monotonic()
    out = sh.email_health(accts, connect=connect)
    elapsed = time.monotonic() - t0
    assert elapsed < 4, f"email_health not bounded: took {elapsed:.1f}s"
    by = {a["name"]: a for a in out["meta"]["accounts"]}
    assert by["slow"]["error"] == "timeout"
