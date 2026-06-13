"""Regression: logs CLI _resolve must tolerate a non-string name.

`_resolve` did `name in p.name` and `p.name == name`; a non-string `name`
(e.g. None) raised TypeError once any *.log file existed. Non-strings now
return None (no match).
"""
from tests.helpers.cli_loader import load_script


def test_non_string_name_returns_none():
    cli = load_script("odysseus-logs")
    assert cli._resolve(None) is None
    assert cli._resolve(123) is None
