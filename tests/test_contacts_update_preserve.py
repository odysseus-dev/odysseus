"""Pin the contact-update preserve logic: partial updates must not wipe
the other field.

The regression: {uid, emails:[new]} with no phones would skip the
existing-contact fetch (AND guard) and silently drop phones.  Same in
reverse.  Fixed by fetching whenever *either* field is missing (OR guard)
and using `force=True` so a just-added contact isn't missed by a stale
cache.
"""
import asyncio

import pytest


def _stub_cc(monkeypatch, contacts_by_uid, *, calls=None):
    """Install a fake contacts_routes module that serves `contacts_by_uid`.

    Returns a ``calls`` list of (action, *args) tuples for assertions.
    """
    import sys
    import types

    call_log = calls if calls is not None else []

    class FakeCC(types.ModuleType):
        pass

    mod = FakeCC("routes.contacts_routes")
    mod._fetch_contacts = lambda force=False: list(contacts_by_uid.values())
    mod._update_contact = lambda uid, name, emails, phones, **kw: call_log.append(
        ("update", uid, name, emails, phones)
    ) or True
    mod._create_contact = lambda name, email, **kw: call_log.append(
        ("create", name, email)
    ) or True
    mod._delete_contact = lambda uid: call_log.append(("delete", uid)) or True

    monkeypatch.setitem(sys.modules, "routes.contacts_routes", mod)
    # Also patch _fetch_contacts on the real module (just in case)
    import routes.contacts_routes as real_cc
    monkeypatch.setattr(real_cc, "_fetch_contacts", mod._fetch_contacts)
    monkeypatch.setattr(real_cc, "_update_contact", mod._update_contact)

    return call_log


@pytest.fixture
def contact_bob():
    return {
        "uid": "bob-001",
        "name": "Bob",
        "emails": ["bob@example.com"],
        "phones": ["+1-555-0100"],
        "address": "123 Main",
    }


# ------------------------------------------------------------------
# Rename-only: both emails + phones survive
# ------------------------------------------------------------------
def test_update_rename_preserves_emails_and_phones(monkeypatch, contact_bob):
    call_log = _stub_cc(monkeypatch, {"bob-001": contact_bob})

    from src.tools.contacts import do_manage_contact
    result = asyncio.run(
        do_manage_contact('{"action":"update","uid":"bob-001","name":"Robert"}')
    )

    assert result.get("output") == "Contact updated."
    assert len(call_log) == 1
    _, uid, name, emails, phones = call_log[0]
    assert name == "Robert"
    assert emails == ["bob@example.com"]   # preserved
    assert phones == ["+1-555-0100"]       # preserved


# ------------------------------------------------------------------
# Update only emails → phones survive
# ------------------------------------------------------------------
def test_update_emails_only_preserves_phones(monkeypatch, contact_bob):
    call_log = _stub_cc(monkeypatch, {"bob-001": contact_bob})

    from src.tools.contacts import do_manage_contact
    result = asyncio.run(
        do_manage_contact(
            '{"action":"update","uid":"bob-001","emails":["new@example.com"]}'
        )
    )

    assert result.get("output") == "Contact updated."
    _, uid, name, emails, phones = call_log[0]
    assert emails == ["new@example.com"]
    assert phones == ["+1-555-0100"]       # preserved, not wiped


# ------------------------------------------------------------------
# Update only phones → emails survive
# ------------------------------------------------------------------
def test_update_phones_only_preserves_emails(monkeypatch, contact_bob):
    call_log = _stub_cc(monkeypatch, {"bob-001": contact_bob})

    from src.tools.contacts import do_manage_contact
    result = asyncio.run(
        do_manage_contact(
            '{"action":"update","uid":"bob-001","phones":["+1-555-9999"]}'
        )
    )

    assert result.get("output") == "Contact updated."
    _, uid, name, emails, phones = call_log[0]
    assert phones == ["+1-555-9999"]
    assert emails == ["bob@example.com"]   # preserved, not wiped


# ------------------------------------------------------------------
# Update both fields explicitly → no fetch needed, both overwritten
# ------------------------------------------------------------------
def test_update_both_fields_explicitly_does_not_fetch(monkeypatch, contact_bob):
    call_log = _stub_cc(monkeypatch, {"bob-001": contact_bob})

    from src.tools.contacts import do_manage_contact
    result = asyncio.run(
        do_manage_contact(
            '{"action":"update","uid":"bob-001","emails":["a@x.com"],"phones":["+0"]}'
        )
    )

    assert result.get("output") == "Contact updated."
    _, uid, name, emails, phones = call_log[0]
    assert emails == ["a@x.com"]
    assert phones == ["+0"]
