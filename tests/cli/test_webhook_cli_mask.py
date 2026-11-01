from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_mask_token_handles_short_values(monkeypatch):
    make_core_db_stub(monkeypatch, models=["ScheduledTask"])
    cli = load_script("odysseus-webhook")

    assert cli._mask_token("") == ""
    assert cli._mask_token("short") == "***"
    assert cli._mask_token("abcdef1234567890") == "abcdef…7890"
    assert cli._mask_token("short", reveal=True) == "short"


def test_task_webhook_url_matches_live_route_and_escapes_path_parts(monkeypatch):
    make_core_db_stub(monkeypatch, models=["ScheduledTask"])
    cli = load_script("odysseus-webhook")

    url = cli._task_webhook_url(
        "https://ody.example/",
        "task/with space",
        "token/with space",
    )

    assert url == (
        "https://ody.example/api/tasks/task%2Fwith%20space/"
        "webhook/token%2Fwith%20space"
    )


def test_default_task_webhook_url_uses_current_task_prefix(monkeypatch):
    make_core_db_stub(monkeypatch, models=["ScheduledTask"])
    cli = load_script("odysseus-webhook")

    assert cli._task_webhook_url(None, "task-1", "secret") == (
        "http://localhost:7000/api/tasks/task-1/webhook/secret"
    )
