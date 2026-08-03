import copy
import json
import runpy
from pathlib import Path

import pytest

from src.rcaf.canonical_store import (
    CanonicalStoreIntegrityError,
    _canonical_bytes,
    _entry_body,
    _sha256,
    _validate_framework_record_dict,
    append_canonical_record,
    validate_and_replay_canonical_store,
)
from src.rcaf.carrier_coalition import (
    COMPONENT_ROLE_NAMES,
    FUTURE_FREEDOM_COMPONENT_NAMES,
    ScaffoldDependenceEvidence,
    TURBULENCE_COMPONENT_NAMES,
)


_RECORD_FACTORY = runpy.run_path(
    "tests/test_rcaf_canonical_store.py"
)["_record"]


def _record_dict() -> dict:
    return _RECORD_FACTORY(1).to_dict()


def _replace_reference_ids(
    value,
    original: str,
    replacement: str,
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key == "reference_contract_id"
                and item == original
            ):
                value[key] = replacement
            else:
                _replace_reference_ids(
                    item,
                    original,
                    replacement,
                )

    elif isinstance(value, list):
        for item in value:
            _replace_reference_ids(
                item,
                original,
                replacement,
            )


def test_sorted_json_round_trip_preserves_carrier_semantics():
    record = _record_dict()

    replayed = json.loads(
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    assert _validate_framework_record_dict(
        replayed
    ) is replayed

    carrier = replayed["carrier_coalition"]

    assert set(
        carrier["role_evidence"][0]["role_vector"]
    ) == set(COMPONENT_ROLE_NAMES)

    assert set(
        carrier[
            "turbulence_channels"
        ][0]["component_vector"]
    ) == set(TURBULENCE_COMPONENT_NAMES)

    assert set(
        carrier[
            "scaffold_dependence"
        ]["dependence_vector"]
    ) == set(
        ScaffoldDependenceEvidence
        .DEPENDENCE_COMPONENT_NAMES
    )

    future_freedom = carrier["future_freedom"]

    for field_name in (
        "baseline_vector",
        "candidate_vector",
        "retention_by_component",
    ):
        assert set(
            future_freedom[field_name]
        ) == set(
            FUTURE_FREEDOM_COMPONENT_NAMES
        )


def test_missing_role_component_is_rejected():
    record = _record_dict()

    role_vector = record[
        "carrier_coalition"
    ]["role_evidence"][0]["role_vector"]

    role_vector.pop(
        COMPONENT_ROLE_NAMES[0]
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match=(
            "carrier role vectors must preserve "
            "all components"
        ),
    ):
        _validate_framework_record_dict(record)


def test_exact_completeness_rejects_count_drift():
    record = _record_dict()

    record["completeness"][
        "required_capability_count"
    ] -= 1

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="canonical record is not complete",
    ):
        _validate_framework_record_dict(record)


def test_exact_completeness_rejects_capability_omission():
    record = _record_dict()

    record["completeness"][
        "satisfied_capabilities"
    ] = record["completeness"][
        "satisfied_capabilities"
    ][:-1]

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="canonical record is not complete",
    ):
        _validate_framework_record_dict(record)


def test_coordinated_carrier_reference_drift_is_rejected():
    record = _record_dict()
    carrier = record["carrier_coalition"]

    original = carrier[
        "coalition"
    ]["reference_contract_id"]

    _replace_reference_ids(
        carrier,
        original,
        "REFERENCE-DRIFTED",
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match=(
            "carrier reference contract does not "
            "match canonical reference conditioning"
        ),
    ):
        _validate_framework_record_dict(record)


def test_carrier_terminal_class_drift_is_rejected():
    record = _record_dict()
    carrier = record["carrier_coalition"]
    drifted = "TERMINAL-DRIFTED"

    carrier["coalition"][
        "terminal_organizational_class_id"
    ] = drifted

    carrier["equivalence_class"][
        "terminal_organizational_class_id"
    ] = drifted

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match=(
            "carrier terminal class does not "
            "match canonical future gate"
        ),
    ):
        _validate_framework_record_dict(record)


def test_framework_boundary_rejects_carrier_authority():
    record = _record_dict()

    record["carrier_coalition"][
        "coalition"
    ]["authority_eligible"] = True

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match=(
            "carrier coalition violates "
            "observer-only authority"
        ),
    ):
        _validate_framework_record_dict(record)


def test_governed_carrier_survives_append_and_replay(
    tmp_path: Path,
):
    root = tmp_path / "valid-ledger"

    receipt = append_canonical_record(
        root,
        ledger_id="R1C-G1-VALID",
        record=_RECORD_FACTORY(1),
    )

    replay = validate_and_replay_canonical_store(
        root,
        ledger_id="R1C-G1-VALID",
    )

    assert receipt.durable_storage_write is True
    assert receipt.authority_posture == "observe_only"
    assert replay.integrity_verified is True
    assert replay.deterministic_replay_verified is True
    assert replay.authority_posture == "observe_only"
    assert replay.record_count == 1

    carrier = replay.records[0][
        "carrier_coalition"
    ]

    assert carrier["coalition"][
        "observer_only"
    ] is True

    assert carrier["coalition"][
        "authority_eligible"
    ] is False

    assert carrier["coalition"][
        "no_auto_promotion"
    ] is True

    authority = carrier[
        "authority_contract"
    ]

    assert authority[
        "authority_status"
    ] == "observe_only"

    assert authority[
        "external_causal_authority"
    ] is False

    assert authority[
        "self_modification_authority"
    ] is False


def test_replay_rejects_hash_valid_semantic_carrier_tamper(
    tmp_path: Path,
):
    root = tmp_path / "tampered-ledger"

    append_canonical_record(
        root,
        ledger_id="R1C-G1-TAMPER",
        record=_RECORD_FACTORY(1),
    )

    segment = root / "segment-000000.jsonl"

    entries = [
        json.loads(line)
        for line in segment.read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert len(entries) == 1

    entry = entries[0]

    entry["record"][
        "carrier_coalition"
    ]["coalition"][
        "authority_eligible"
    ] = True

    entry["entry_sha256"] = _sha256(
        _canonical_bytes(
            _entry_body(entry)
        )
    )

    segment.write_bytes(
        _canonical_bytes(entry) + b"\n"
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match=(
            "carrier coalition violates "
            "observer-only authority"
        ),
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="R1C-G1-TAMPER",
            rebuild_manifest=False,
        )
