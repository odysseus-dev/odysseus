# tests/test_platform_db.py
"""Platform core: ORM tables exist and round-trip."""
from core.database import (
    SessionLocal, Company, Principal, EnvelopeRecord, GatedIntent,
)


def test_company_principal_roundtrip():
    db = SessionLocal()
    try:
        c = Company(id="travel-1", vertical_type="travel_agency",
                    display_name="Travel One", surface_policy="web_first")
        db.add(c)
        p = Principal(id="human:oleg", kind="human", company_id="travel-1",
                      is_manager=True)
        db.add(p)
        db.commit()
        got = db.query(Company).filter_by(id="travel-1").one()
        assert got.vertical_type == "travel_agency"
        mgr = db.query(Principal).filter_by(company_id="travel-1",
                                            is_manager=True).one()
        assert mgr.kind == "human"
    finally:
        db.rollback()
        db.close()


def test_envelope_record_and_gated_intent_tables():
    db = SessionLocal()
    try:
        # Self-contained: own company so FK checks pass regardless of
        # test ordering (don't lean on travel-1 from the other test).
        db.add(Company(id="travel-db2", vertical_type="travel_agency",
                       display_name="Travel DB2", surface_policy="web_first"))
        rec = EnvelopeRecord(message_id="m-1", conversation_id="c-1",
                             from_company="travel-db2", to_company="bigboss",
                             intent="status.report", status="finished",
                             payload_json="{}", signature="sig",
                             audit_hash="h1", prev_audit_hash="GENESIS")
        db.add(rec)
        gi = GatedIntent(id="gi-1", envelope_message_id="m-1",
                         company_id="travel-db2", gated_class="booking",
                         state="proposed")
        db.add(gi)
        db.commit()
        assert db.query(EnvelopeRecord).count() >= 1
        assert db.query(GatedIntent).filter_by(state="proposed").count() >= 1
    finally:
        db.rollback()
        db.close()
