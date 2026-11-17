"""set_privileges rejects mistyped values instead of storing them (issue #6829)."""

import pytest

from tests.test_set_admin import _fresh_auth_manager


@pytest.mark.parametrize("privs", [
    {"can_use_bash": "no"},
    {"can_use_bash": 1},
    {"max_messages_per_day": "abc"},
    {"max_messages_per_day": True},
    {"allowed_models": "gpt"},
    {"block_all_models": None},
])
def test_set_privileges_rejects_mistyped_values(tmp_path, privs):
    _, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("bob", "pw-123456")
    before = dict(mgr.get_privileges("bob"))

    with pytest.raises(ValueError):
        mgr.set_privileges("bob", privs)

    assert mgr.get_privileges("bob") == before


def test_set_privileges_accepts_well_typed_values(tmp_path):
    _, mgr = _fresh_auth_manager(tmp_path)
    mgr.create_user("bob", "pw-123456")

    assert mgr.set_privileges("bob", {
        "can_use_bash": True,
        "max_messages_per_day": 25,
        "allowed_models": ["gpt"],
        "unknown_key": "ignored",
    }) is True

    privs = mgr.get_privileges("bob")
    assert privs["can_use_bash"] is True
    assert privs["max_messages_per_day"] == 25
    assert privs["allowed_models"] == ["gpt"]
    assert "unknown_key" not in privs
