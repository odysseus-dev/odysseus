"""Company/principal registry. Spec §2.

Rules enforced here:
- top-level company (no parent) requires >= 1 human manager;
- a company with 0 humans is a department -> must have parent_id (policy owner);
- principals are typed: human | agent | company_service_account;
- creating a company issues its Ed25519 service identity.
"""
from typing import Optional

from core.database import get_db_session, Company, Principal
from .envelope import keypair_pem

PRINCIPAL_KINDS = {"human", "agent", "company_service_account"}


class RegistryError(ValueError):
    pass


def _company_dict(c: Company, principals=None) -> dict:
    return {
        "id": c.id, "vertical_type": c.vertical_type,
        "display_name": c.display_name, "parent_id": c.parent_id,
        "surface_policy": c.surface_policy, "is_active": c.is_active,
        "principals": [
            {"id": p.id, "kind": p.kind, "is_manager": p.is_manager}
            for p in (principals or [])
        ],
    }


def create_company(company_id: str, vertical_type: str, display_name: str,
                   manager_principal_id: Optional[str],
                   parent_id: Optional[str] = None,
                   surface_policy: str = "web_first") -> dict:
    if manager_principal_id is None and parent_id is None:
        raise RegistryError(
            "top-level company requires a human manager; "
            "a 0-manager company is a department and needs parent_id")
    priv_pem, pub_pem = keypair_pem()
    with get_db_session() as db:
        if parent_id and not db.get(Company, parent_id):
            raise RegistryError(f"parent company {parent_id!r} not found")
        c = Company(id=company_id, vertical_type=vertical_type,
                    display_name=display_name, parent_id=parent_id,
                    surface_policy=surface_policy,
                    public_key_pem=pub_pem, private_key_pem=priv_pem)
        db.add(c)
        db.add(Principal(id=f"svc:{company_id}", kind="company_service_account",
                         company_id=company_id, is_manager=False))
        if manager_principal_id:
            db.add(Principal(id=manager_principal_id, kind="human",
                             company_id=company_id, is_manager=True))
        db.commit()
        return _company_dict(c)


def get_company(company_id: str) -> Optional[dict]:
    with get_db_session() as db:
        c = db.get(Company, company_id)
        if not c:
            return None
        ps = db.query(Principal).filter_by(company_id=company_id).all()
        return _company_dict(c, ps)


def company_public_key(company_id: str) -> Optional[str]:
    with get_db_session() as db:
        c = db.get(Company, company_id)
        return c.public_key_pem if c else None


def company_private_key(company_id: str) -> Optional[str]:
    """Used by the company runtime side (and tests) to sign envelopes."""
    with get_db_session() as db:
        c = db.get(Company, company_id)
        return c.private_key_pem if c else None


def add_principal(company_id: str, principal_id: str, kind: str,
                  is_manager: bool = False) -> dict:
    if kind not in PRINCIPAL_KINDS:
        raise RegistryError(f"unknown principal kind {kind!r}")
    with get_db_session() as db:
        if not db.get(Company, company_id):
            raise RegistryError(f"company {company_id!r} not found")
        p = Principal(id=principal_id, kind=kind, company_id=company_id,
                      is_manager=is_manager)
        db.add(p)
        db.commit()
        return {"id": p.id, "kind": p.kind, "is_manager": p.is_manager}


def ensure_manager(company_id: str, principal_id: str,
                   kind: str = "human") -> None:
    """Idempotently make `principal_id` a manager of `company_id`.

    Adds the (principal, company) binding if missing, or promotes an existing
    non-manager binding. Used so a company with shared ownership (e.g. Big
    Boss across mission owners) grants approval rights to each owner.
    """
    if kind not in PRINCIPAL_KINDS:
        raise RegistryError(f"unknown principal kind {kind!r}")
    with get_db_session() as db:
        if not db.get(Company, company_id):
            raise RegistryError(f"company {company_id!r} not found")
        p = (db.query(Principal)
               .filter_by(id=principal_id, company_id=company_id).first())
        if p is None:
            db.add(Principal(id=principal_id, kind=kind,
                             company_id=company_id, is_manager=True))
        elif not p.is_manager:
            p.is_manager = True
        db.commit()


def is_manager_of(principal_id: str, company_id: str) -> bool:
    with get_db_session() as db:
        p = (db.query(Principal)
               .filter_by(id=principal_id, company_id=company_id, is_manager=True)
               .first())
        return p is not None
