"""An OAuth connection must be visible to the sync that uses it.

Three different owner spellings reach the Graph modules for the same person
on a single-user install:

  * the OAuth routes see `require_user(request)`, which returns `""`;
  * the calendar routes normalize that to ODYSSEUS_FALLBACK_OWNER so rows
    have a stable owner to filter on;
  * preferences use `None` for the flat/first record.

Storing under one and reading under another produces the worst possible
failure: `/sync` finds no connected account, and "no account connected" is
not an error, so the button reports success and does nothing. These tests pin
the normalization that keeps all three pointing at one record.
"""

import pytest

from src import msgraph_calendar as mg
from src import msgraph_todo as mt

MODULES = [
    pytest.param(mg, "msgraph_accounts", "_load_msgraph_accounts",
                 "_save_msgraph_accounts", id="calendar"),
    pytest.param(mt, "mstodo_accounts", "_load_mstodo_accounts",
                 "_save_mstodo_accounts", id="todo"),
]


@pytest.fixture
def prefs(monkeypatch):
    """An in-memory prefs store that records the key it was asked for."""
    store: dict = {}
    monkeypatch.setattr("routes.prefs_routes._load_for_user",
                        lambda user=None: dict(store.get(user) or {}))
    monkeypatch.setattr("routes.prefs_routes._save_for_user",
                        lambda user, values: store.__setitem__(user, dict(values)))
    return store


@pytest.mark.parametrize("module,key,load_name,save_name", MODULES)
@pytest.mark.parametrize("reader", ["", None, "owner@localhost"])
def test_an_account_connected_anonymously_is_found_by_every_spelling(
    prefs, module, key, load_name, save_name, reader,
):
    """The OAuth callback stores under "" — the sync must still find it."""
    save = getattr(module, save_name)
    load = getattr(module, load_name)
    save("", [{"id": "a1"}])
    assert [a["id"] for a in load(reader)] == ["a1"]


@pytest.mark.parametrize("module,key,load_name,save_name", MODULES)
def test_all_three_spellings_write_to_one_record(
    prefs, module, key, load_name, save_name,
):
    save = getattr(module, save_name)
    save("", [{"id": "a1"}])
    save("owner@localhost", [{"id": "a1"}, {"id": "a2"}])
    # One record, not three: the fallback name and the empty owner are the
    # same person, so they must not each get their own account list.
    assert list(prefs) == [None]
    assert len(prefs[None][key]) == 2


@pytest.mark.parametrize("module,key,load_name,save_name", MODULES)
def test_a_named_owner_keeps_their_own_record(
    prefs, module, key, load_name, save_name,
):
    """Normalization is only for the single-user case; a real account must
    not read another's connection."""
    save = getattr(module, save_name)
    load = getattr(module, load_name)
    save("alice", [{"id": "a-alice"}])
    save("", [{"id": "a-anon"}])
    assert [a["id"] for a in load("alice")] == ["a-alice"]
    assert [a["id"] for a in load("")] == ["a-anon"]


@pytest.mark.parametrize("module,key,load_name,save_name", MODULES)
def test_a_custom_fallback_owner_is_honoured(
    prefs, monkeypatch, module, key, load_name, save_name,
):
    monkeypatch.setenv("ODYSSEUS_FALLBACK_OWNER", "house@lan")
    save = getattr(module, save_name)
    load = getattr(module, load_name)
    save("", [{"id": "a1"}])
    assert [a["id"] for a in load("house@lan")] == ["a1"]


@pytest.mark.parametrize("module,key,load_name,save_name", MODULES)
def test_a_deployment_without_the_env_var_uses_the_documented_default(
    prefs, monkeypatch, module, key, load_name, save_name,
):
    monkeypatch.delenv("ODYSSEUS_FALLBACK_OWNER", raising=False)
    save = getattr(module, save_name)
    load = getattr(module, load_name)
    save("", [{"id": "a1"}])
    assert [a["id"] for a in load("owner@localhost")] == ["a1"]


def test_the_calendar_fallback_owner_matches_the_route_layers():
    """If these drift the connection becomes invisible again."""
    import routes.calendar_routes as croutes

    assert mg._prefs_owner(croutes.FALLBACK_OWNER) is None
    assert mt._prefs_owner(croutes.FALLBACK_OWNER) is None
