"""Mail credentials copied with NBSP separators must not crash IMAP login."""

import os
import tempfile
from pathlib import Path

_tmp_data = Path(tempfile.mkdtemp(prefix="odysseus_email_cred_nbsp_"))
os.environ.setdefault("DATA_DIR", str(_tmp_data))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_data / 'app.db'}")

from routes import email_helpers as eh


class FakeImap:
    def __init__(self):
        self.login_args = None

    def login(self, user, password):
        self.login_args = (user, password)


def test_imap_connect_normalizes_nbsp_credentials(monkeypatch):
    fake = FakeImap()
    monkeypatch.setattr(
        eh,
        "_get_email_config",
        lambda account_id=None, owner="": {
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "imap_starttls": False,
            "imap_user": "user\xa0@example.test",
            "imap_password": "abcd\xa0efgh",
        },
    )
    monkeypatch.setattr(eh, "_open_imap_connection", lambda *args, **kwargs: fake)

    assert eh._imap_connect() is fake
    assert fake.login_args == ("user @example.test", "abcdefgh")
