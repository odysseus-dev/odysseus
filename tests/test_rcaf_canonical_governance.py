# ============================================================
# tests/test_rcaf_canonical_governance.py
# ============================================================

from __future__ import annotations

import importlib.util
import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from src.rcaf.canonical_evolution import (
    CURRENT_SCHEMA_BY_FAMILY,
    CanonicalSchemaEvolutionError,
    LosslessMigrationManifest,
    evaluate_schema,
    require_current_schema,
    validate_canonical_record_schema_set,
)

from src.rcaf.canonical_store import (
    CanonicalStoreIntegrityError,
    CanonicalStoreQuarantinedError,
    append_canonical_record,
    quarantine_canonical_store,
    validate_and_replay_canonical_store,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

STORE_FIXTURE_PATH = (
    REPO_ROOT
    / "tests"
    / "test_rcaf_canonical_store.py"
)


def _load_store_fixture():
    spec = importlib.util.spec_from_file_location(
        "rcaf_governance_store_fixture",
        STORE_FIXTURE_PATH,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "unable to load canonical-store fixture"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    return module


def _record(
    index: int,
):
    return _load_store_fixture()._record(
        index
    )


def test_current_schema_set_is_explicitly_accepted():
    validation = (
        validate_canonical_record_schema_set(
            _record(1).to_dict()
        )
    )

    assert validation.current is True

    assert validation.to_dict()[
        "automatic_migration_allowed"
    ] is False


def test_previous_canonical_schema_requires_manifest():
    evaluation = evaluate_schema(
        "canonical_record",
        "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.2",
    )

    assert evaluation.disposition == (
        "migration_required"
    )

    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="explicit lossless migration manifest",
    ):
        require_current_schema(
            "canonical_record",
            (
                "RCAF-CANONICAL-"
                "FRAMEWORK-LEDGER-0.2"
            ),
        )


def test_unknown_schema_is_rejected():
    evaluation = evaluate_schema(
        "canonical_record",
        "RCAF-CANONICAL-FRAMEWORK-LEDGER-9.9",
    )

    assert evaluation.disposition == (
        "unsupported"
    )

    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="schema is unsupported",
    ):
        require_current_schema(
            "canonical_record",
            (
                "RCAF-CANONICAL-"
                "FRAMEWORK-LEDGER-9.9"
            ),
        )


def _migration_manifest():
    return LosslessMigrationManifest(
        migration_id=(
            "MIGRATION-CANONICAL-02-03"
        ),
        family="canonical_record",
        source_schema=(
            "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.2"
        ),
        target_schema=(
            CURRENT_SCHEMA_BY_FAMILY[
                "canonical_record"
            ]
        ),
        source_record_sha256="a" * 64,
        target_record_sha256="b" * 64,
        preserved_field_paths=(
            "record_id",
            "event_spine_id",
            "authority_posture",
            "organizational_record",
        ),
        added_explicit_field_paths=(
            "future_authority_evidence",
        ),
    )


def test_lossless_migration_manifest_is_deterministic():
    first = (
        _migration_manifest()
        .canonical_json()
    )

    second = (
        _migration_manifest()
        .canonical_json()
    )

    assert first == second

    data = json.loads(
        first
    )

    assert data["lossless"] is True
    assert data["automatic_migration"] is False
    assert data["dropped_field_paths"] == []
    assert data["inferred_field_paths"] == []


def test_migration_cannot_drop_fields():
    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="cannot drop fields",
    ):
        replace(
            _migration_manifest(),
            dropped_field_paths=(
                "future_gate",
            ),
        )


def test_migration_cannot_infer_missing_rcaf_fields():
    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="cannot infer missing RCAF fields",
    ):
        replace(
            _migration_manifest(),
            inferred_field_paths=(
                "future_authority_evidence",
            ),
        )


def test_migration_cannot_change_authority_posture():
    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="cannot change authority posture",
    ):
        replace(
            _migration_manifest(),
            authority_posture_after="bounded",
        )


def _tamper_store(
    root: Path,
) -> bytes:
    append_canonical_record(
        root,
        ledger_id="QUARANTINE-LEDGER",
        record=_record(1),
    )

    segment = (
        root
        / "segment-000000.jsonl"
    )

    entry = json.loads(
        segment.read_text(
            encoding="utf-8"
        )
    )

    entry["record"]["record_id"] = (
        "RCAF-FORENSIC-TAMPER"
    )

    segment.write_text(
        json.dumps(
            entry,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    return segment.read_bytes()


def test_quarantine_preserves_exact_bytes_and_blocks_store(
    tmp_path,
):
    root = tmp_path / "ledger"

    tampered_bytes = _tamper_store(
        root
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="entry SHA-256 mismatch",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="QUARANTINE-LEDGER",
        )

    receipt = quarantine_canonical_store(
        root,
        ledger_id="QUARANTINE-LEDGER",
        case_id="CASE-HASH-MISMATCH-0001",
        reason_code="ENTRY-HASH-MISMATCH",
    )

    case_directory = Path(
        receipt.case_directory
    )

    snapshot = (
        case_directory
        / "segment-000000.jsonl"
    )

    assert snapshot.read_bytes() == (
        tampered_bytes
    )

    assert receipt.append_blocked is True
    assert receipt.authority_posture == (
        "observe_only"
    )

    with pytest.raises(
        CanonicalStoreQuarantinedError,
        match="canonical store is quarantined",
    ):
        append_canonical_record(
            root,
            ledger_id="QUARANTINE-LEDGER",
            record=_record(2),
        )

    with pytest.raises(
        CanonicalStoreQuarantinedError,
        match="canonical store is quarantined",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="QUARANTINE-LEDGER",
        )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="entry SHA-256 mismatch",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="QUARANTINE-LEDGER",
            allow_quarantined=True,
        )


def test_quarantine_filesystem_is_private(
    tmp_path,
):
    root = tmp_path / "ledger"

    _tamper_store(
        root
    )

    receipt = quarantine_canonical_store(
        root,
        ledger_id="QUARANTINE-LEDGER",
        case_id="CASE-PRIVATE-0001",
        reason_code="ENTRY-HASH-MISMATCH",
    )

    case_directory = Path(
        receipt.case_directory
    )

    forensic_root = (
        case_directory.parent
    )

    assert (
        stat.S_IMODE(
            os.stat(forensic_root).st_mode
        )
        == 0o700
    )

    assert (
        stat.S_IMODE(
            os.stat(case_directory).st_mode
        )
        == 0o700
    )

    for path in case_directory.iterdir():
        assert (
            stat.S_IMODE(
                os.stat(path).st_mode
            )
            == 0o600
        )

    marker = (
        root
        / "quarantine.json"
    )

    assert (
        stat.S_IMODE(
            os.stat(marker).st_mode
        )
        == 0o600
    )


def test_quarantine_does_not_follow_symlinks(
    tmp_path,
):
    root = tmp_path / "ledger"

    _tamper_store(
        root
    )

    outside = (
        tmp_path
        / "outside-secret"
    )

    outside.write_text(
        "must-not-be-copied",
        encoding="utf-8",
    )

    (
        root
        / "unexpected-link"
    ).symlink_to(
        outside
    )

    receipt = quarantine_canonical_store(
        root,
        ledger_id="QUARANTINE-LEDGER",
        case_id="CASE-SYMLINK-0001",
        reason_code="UNSAFE-ENTRY",
    )

    case_directory = Path(
        receipt.case_directory
    )

    manifest = json.loads(
        (
            case_directory
            / "forensic_manifest.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    symlink_entries = [
        entry
        for entry in manifest["entries"]
        if entry["name"]
        == "unexpected-link"
    ]

    assert len(
        symlink_entries
    ) == 1

    assert (
        symlink_entries[0][
            "entry_type"
        ]
        == "symlink"
    )

    assert (
        symlink_entries[0][
            "copied"
        ]
        is False
    )

    assert (
        symlink_entries[0][
            "target_followed"
        ]
        is False
    )

    assert not (
        case_directory
        / "unexpected-link"
    ).exists()


def test_existing_quarantine_cannot_be_overwritten(
    tmp_path,
):
    root = tmp_path / "ledger"

    _tamper_store(
        root
    )

    quarantine_canonical_store(
        root,
        ledger_id="QUARANTINE-LEDGER",
        case_id="CASE-IMMUTABLE-0001",
        reason_code="ENTRY-HASH-MISMATCH",
    )

    with pytest.raises(
        CanonicalStoreQuarantinedError,
        match="canonical store is quarantined",
    ):
        quarantine_canonical_store(
            root,
            ledger_id="QUARANTINE-LEDGER",
            case_id="CASE-IMMUTABLE-0002",
            reason_code="SECOND-QUARANTINE",
        )
