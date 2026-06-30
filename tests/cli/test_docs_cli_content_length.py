from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_text_len_ignores_non_string_values(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Document", "DocumentVersion"])
    cli = load_script("odysseus-docs")

    assert cli._text_len("hello") == 5
    assert cli._text_len(None) == 0
    assert cli._text_len({"bad": "row"}) == 0
