"""A pooled IMAP handle that changed a mailbox must not be reused, whatever the
provider: it can keep serving the pre-change view (seen on Outlook.com, where
it never saw new mail again after a delete)."""
from routes.email import email_routes as er


class FakeConn:
    def __init__(self, readonly):
        self.state = "SELECTED"
        self.is_readonly = readonly
        self.logged_out = False

    def noop(self):
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


def test_read_write_handle_is_closed_and_read_only_handle_is_pooled(monkeypatch):
    pool = er.setup_email_routes()._email_pool
    monkeypatch.setattr(er, "_imap_connect", lambda *a, **k: "fresh")

    after_delete = FakeConn(readonly=False)
    pool["release"]("acct", after_delete, owner="jo")
    assert after_delete.logged_out
    assert pool["connect"]("acct", owner="jo") == ("fresh", False)

    after_list = FakeConn(readonly=True)
    pool["release"]("acct", after_list, owner="jo")
    assert not after_list.logged_out
    assert pool["connect"]("acct", owner="jo") == (after_list, True)
