import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


class _Conn:
    def select(self, folder, readonly=True):
        return "OK", [b"1"]

    def fetch(self, uid, spec):
        # IMAP can return OK with an empty payload (UID expunged mid-session).
        return "OK", []


class _ImapCtx:
    def __init__(self, account):
        pass

    def __enter__(self):
        return _Conn()

    def __exit__(self, *a):
        return False


def _load_mail_cli(monkeypatch):
    helpers = ModuleType("routes.email_helpers")
    helpers._imap = _ImapCtx
    helpers._get_email_config = lambda account=None: {}
    helpers._decode_header = lambda value: value
    helpers._extract_text = lambda msg: ""
    helpers._extract_html = lambda msg: ""
    helpers._list_attachments_from_msg = lambda msg: []
    pollers = ModuleType("routes.email_pollers")
    pollers._scheduled_poll_once = lambda: {}
    pollers._run_auto_summarize_once = lambda **kwargs: ""
    monkeypatch.setitem(sys.modules, "routes.email_helpers", helpers)
    monkeypatch.setitem(sys.modules, "routes.email_pollers", pollers)
    make_core_db_stub(
        monkeypatch,
        attributes={"SessionLocal": object, "EmailAccount": object},
        install_core_package=True,
    )
    return load_script("odysseus-mail")


def test_cmd_read_handles_empty_fetch_payload(monkeypatch):
    cli = _load_mail_cli(monkeypatch)
    args = SimpleNamespace(account="acc", folder="INBOX", uid="5", html=False)
    # old code did raw = msg_data[0][1] on the empty list and raised IndexError;
    # the guard turns it into a clean fail() (SystemExit).
    with pytest.raises(SystemExit):
        cli.cmd_read(args)
