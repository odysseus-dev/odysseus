from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_mask_token_handles_short_values(monkeypatch):
    make_core_db_stub(monkeypatch, models=["ScheduledTask"])
    cli = load_script("odysseus-webhook")

    assert cli._mask_token("") == ""
    assert cli._mask_token("short") == "***"
    assert cli._mask_token("abcdef1234567890") == "abcdef…7890"
    assert cli._mask_token("short", reveal=True) == "short"


def test_summary_ignores_non_string_webhook_token(monkeypatch):
    make_core_db_stub(monkeypatch, models=["ScheduledTask"])
    cli = load_script("odysseus-webhook")
    task = SimpleNamespace(id="t1", name="run", status="ready", task_type="once", webhook_token=123)

    out = cli._summary(task)

    assert out["webhook_token"] == ""
    assert out["has_token"] is False
