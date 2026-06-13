# tests/test_platform_registry.py
"""Registry: company CRUD, manager rule, key issuance, department rule."""
import pytest

from services.business_platform.registry import (
    create_company, get_company, add_principal, company_public_key, RegistryError,
)


def test_create_company_issues_keypair_and_service_account():
    c = create_company("travel-reg-1", "travel_agency", "Travel Reg",
                       manager_principal_id="human:oleg")
    assert c["id"] == "travel-reg-1"
    pub = company_public_key("travel-reg-1")
    assert pub and "BEGIN PUBLIC KEY" in pub
    # service account principal auto-created
    got = get_company("travel-reg-1")
    kinds = {p["kind"] for p in got["principals"]}
    assert "company_service_account" in kinds
    assert "human" in kinds


def test_top_level_company_requires_human_manager():
    with pytest.raises(RegistryError):
        create_company("orphan-1", "travel_agency", "No Manager",
                       manager_principal_id=None)


def test_department_zero_humans_needs_parent():
    create_company("hq-1", "travel_agency", "HQ", manager_principal_id="human:oleg")
    d = create_company("dept-1", "travel_agency", "Desk",
                       manager_principal_id=None, parent_id="hq-1")
    assert d["parent_id"] == "hq-1"


def test_add_principal_typed():
    create_company("travel-reg-2", "travel_agency", "T2",
                   manager_principal_id="human:oleg")
    p = add_principal("travel-reg-2", "agent:travel-reg-2/booker", "agent")
    assert p["kind"] == "agent"
    with pytest.raises(RegistryError):
        add_principal("travel-reg-2", "weird:thing", "alien_kind")
