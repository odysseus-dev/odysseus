from dataclasses import replace
import json

import pytest

from src.rcaf.canonical_evolution import (
    CURRENT_SCHEMA_BY_FAMILY,
    KNOWN_PREVIOUS_SCHEMAS_BY_FAMILY,
    RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS,
    RCAF_SCHEMA_EVOLUTION_SCHEMA,
    CanonicalSchemaEvolutionError,
    LosslessMigrationManifest,
    evaluate_schema,
)


CANONICAL_0_2 = (
    "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.2"
)

CANONICAL_0_3 = (
    "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.3"
)


def _manifest(
    *,
    source_schema: str,
    added_paths: tuple[str, ...],
) -> LosslessMigrationManifest:
    return LosslessMigrationManifest(
        migration_id=(
            "MIGRATION-CANONICAL-INTEGRITY"
        ),
        family="canonical_record",
        source_schema=source_schema,
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
        added_explicit_field_paths=added_paths,
    )


def test_evolution_registry_is_exact():
    assert RCAF_SCHEMA_EVOLUTION_SCHEMA == (
        "RCAF-SCHEMA-EVOLUTION-0.2"
    )

    assert set(CURRENT_SCHEMA_BY_FAMILY) == {
        "canonical_record",
        "carrier_coalition",
        "future_authority",
        "organizational_record",
    }

    assert KNOWN_PREVIOUS_SCHEMAS_BY_FAMILY[
        "canonical_record"
    ] == frozenset(
        {
            CANONICAL_0_2,
            CANONICAL_0_3,
        }
    )

    assert KNOWN_PREVIOUS_SCHEMAS_BY_FAMILY[
        "carrier_coalition"
    ] == frozenset()


def test_canonical_0_3_requires_explicit_migration():
    evaluation = evaluate_schema(
        "canonical_record",
        CANONICAL_0_3,
    )

    assert evaluation.disposition == (
        "migration_required"
    )

    assert (
        evaluation.automatic_migration_allowed
        is False
    )


def test_0_3_to_0_4_carrier_paths_are_exact():
    assert (
        RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS
        == (
            "carrier_coalition",
            "carrier_coalition.coalition",
            "carrier_coalition.validity_relations",
            "carrier_coalition.role_evidence",
            "carrier_coalition.scaffold_dependence",
            "carrier_coalition.scaffold_release",
            "carrier_coalition.turbulence_channels",
            "carrier_coalition.future_freedom",
            "carrier_coalition.matched_experiment",
            "carrier_coalition.equivalence_class",
            "carrier_coalition.nomination",
            "carrier_coalition.authority_contract",
        )
    )


def test_0_3_migration_rejects_missing_carrier_path():
    incomplete = (
        RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS[
            :-1
        ]
    )

    with pytest.raises(
        CanonicalSchemaEvolutionError,
    ):
        _manifest(
            source_schema=CANONICAL_0_3,
            added_paths=incomplete,
        )


def test_0_3_migration_accepts_full_carrier_path_set():
    manifest = _manifest(
        source_schema=CANONICAL_0_3,
        added_paths=(
            RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS
        ),
    )

    data = manifest.to_dict()

    assert tuple(
        data["added_explicit_field_paths"]
    ) == (
        RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS
    )

    assert data["lossless"] is True
    assert data["automatic_migration"] is False
    assert data["dropped_field_paths"] == []
    assert data["inferred_field_paths"] == []

    first = manifest.canonical_json()
    second = manifest.canonical_json()

    assert first == second
    assert json.loads(first) == data


def test_registered_0_2_migration_remains_accepted():
    manifest = _manifest(
        source_schema=CANONICAL_0_2,
        added_paths=(
            "future_authority_evidence",
        ),
    )

    assert manifest.source_schema == CANONICAL_0_2
    assert manifest.lossless is True
    assert manifest.automatic_migration is False


def test_0_3_migration_cannot_infer_carrier_evidence():
    manifest = _manifest(
        source_schema=CANONICAL_0_3,
        added_paths=(
            RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS
        ),
    )

    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="cannot infer missing RCAF fields",
    ):
        replace(
            manifest,
            inferred_field_paths=(
                "carrier_coalition.future_freedom",
            ),
        )


def test_0_3_migration_cannot_change_authority():
    manifest = _manifest(
        source_schema=CANONICAL_0_3,
        added_paths=(
            RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS
        ),
    )

    with pytest.raises(
        CanonicalSchemaEvolutionError,
        match="cannot change authority posture",
    ):
        replace(
            manifest,
            authority_posture_after="bounded",
        )
