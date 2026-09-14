"""Canonical model-control record parsing tests."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import model_capabilities as mc
from src.model_control_capabilities import (
    allowed_control_values,
    parse_catalog_records,
    record_for_model,
    resolve_cached_model_record,
)


def test_allowed_values_require_claimed_or_verified_canonical_control():
    record = {
        "model_id": "opaque",
        "deterministic_controls": [{
            "control": mc.CONTROL_REASONING_EFFORT,
            "status": mc.ASSERTION_CLAIMED,
            "source": mc.SOURCE_PROVIDER_READER,
            "confidence": mc.CONFIDENCE_PROVIDER_REPORTED,
            "evidence": {"allowed_values": ["low", "not valid!", "high", "high"]},
        }],
    }

    assert allowed_control_values(record, mc.CONTROL_REASONING_EFFORT) == ("low", "high")
    record["deterministic_controls"][0]["status"] = mc.ASSERTION_UNKNOWN
    assert allowed_control_values(record, mc.CONTROL_REASONING_EFFORT) == ()


def test_catalog_parsing_and_lookup_fail_closed_on_invalid_or_duplicate_records():
    valid = {"model_id": "one", "deterministic_controls": []}

    assert parse_catalog_records("not-json") == []
    assert record_for_model([valid], "one") == valid
    assert record_for_model([valid, dict(valid)], "one") is None
    assert record_for_model([valid], "two") is None


def test_runtime_resolution_prefers_endpoint_identity_and_rejects_url_conflicts(monkeypatch):
    import core.database as database

    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    low = {"model_id": "same", "deterministic_controls": [{
        "control": "reasoning_effort", "status": "claimed", "evidence": {"allowed_values": ["low"]},
    }]}
    high = {"model_id": "same", "deterministic_controls": [{
        "control": "reasoning_effort", "status": "claimed", "evidence": {"allowed_values": ["high"]},
    }]}
    db = Session()
    try:
        db.add_all([
            database.ModelEndpoint(
                id="one", name="One", base_url="https://chatgpt.com/backend-api/codex",
                is_enabled=True, cached_model_capabilities=json.dumps([low]),
            ),
            database.ModelEndpoint(
                id="two", name="Two", base_url="https://chatgpt.com/backend-api/codex",
                is_enabled=True, cached_model_capabilities=json.dumps([high]),
            ),
        ])
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(database, "SessionLocal", Session)

    exact = resolve_cached_model_record(
        endpoint_id="two",
        endpoint_url="https://chatgpt.com/backend-api/codex/responses",
        model="same",
    )
    ambiguous = resolve_cached_model_record(
        endpoint_id=None,
        endpoint_url="https://chatgpt.com/backend-api/codex/responses",
        model="same",
    )

    assert allowed_control_values(exact, "reasoning_effort") == ("high",)
    assert ambiguous is None
