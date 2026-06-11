import sys
from types import ModuleType

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def _load_mail_cli(monkeypatch):
    helpers = ModuleType("routes.email_helpers")
    helpers._imap = object
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


def test_recipient_list_trims_to_cc_and_bcc(monkeypatch):
    cli = _load_mail_cli(monkeypatch)

    assert cli._recipient_list(" a@example.com, ", "b@example.com", " c@example.com ") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_recipient_list_rejects_empty_envelope(monkeypatch):
    cli = _load_mail_cli(monkeypatch)

    try:
        cli._recipient_list(" , ", "", "")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected empty recipient list to exit")
