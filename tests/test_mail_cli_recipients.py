import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


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

    core_mod = ModuleType("core")
    database_mod = ModuleType("core.database")
    database_mod.SessionLocal = object
    database_mod.EmailAccount = object

    monkeypatch.setitem(sys.modules, "routes.email_helpers", helpers)
    monkeypatch.setitem(sys.modules, "routes.email_pollers", pollers)
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.database", database_mod)

    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-mail"
    loader = importlib.machinery.SourceFileLoader("odysseus_mail_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
