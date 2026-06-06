from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_preview_text_ignores_non_string_values(monkeypatch):
    make_core_db_stub(monkeypatch, models=["ScheduledTask", "TaskRun"])
    cli = load_script("odysseus-tasks")

    assert cli._preview_text(None) == ""
    assert cli._preview_text({"bad": "row"}) == ""
    assert cli._preview_text("x" * 201) == ("x" * 200) + "…"
