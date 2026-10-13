import pytest


@pytest.mark.asyncio
async def test_broad_contact_listing_is_bounded(monkeypatch):
    import routes.contacts_routes as contacts
    from src.tools.contacts import do_manage_contact

    rows = [
        {"name": f"Contact {index}", "emails": [f"c{index}@example.test"], "phones": [], "uid": str(index)}
        for index in range(25)
    ]
    monkeypatch.setattr(contacts, "_fetch_contacts", lambda *args, **kwargs: rows)

    result = await do_manage_contact('{"action":"list"}', owner="test-owner")

    assert result["exit_code"] == 0
    assert result["output"].startswith("Showing 20 of 25 contacts:")
    assert "Contact 19" in result["output"]
    assert "Contact 20" not in result["output"]
    assert "...and 5 more" in result["output"]


@pytest.mark.asyncio
async def test_exact_contact_search_is_not_bounded(monkeypatch):
    import routes.contacts_routes as contacts
    from src.tools.contacts import do_manage_contact

    rows = [
        {"name": "Target Contact", "emails": ["target@example.test"], "phones": [], "uid": "target"},
        {"name": "Other Contact", "emails": ["other@example.test"], "phones": [], "uid": "other"},
    ]
    monkeypatch.setattr(contacts, "_fetch_contacts", lambda *args, **kwargs: rows)

    result = await do_manage_contact(
        '{"action":"search","query":"target"}', owner="test-owner"
    )

    assert result["exit_code"] == 0
    assert "Target Contact" in result["output"]
    assert "...and" not in result["output"]
