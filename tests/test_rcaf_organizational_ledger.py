# ============================================================
# tests/test_rcaf_organizational_ledger.py
# ============================================================

from __future__ import annotations

import inspect
import json

import pytest

from src.rcaf.organizational_ledger import (
    RCAF_ATLAS_TYPES,
    AtlasLink,
    BoundaryAuthority,
    CausalFlow,
    CoherenceGeometry,
    CounterfactualComparison,
    EvidenceGeometry,
    FutureConditioning,
    MetricVector,
    ParticipationGeometry,
    RCAFOrganizationalLedgerError,
    RCAFOrganizationalRecord,
    RealizationLifecycle,
    ReferenceGeometry,
)


def _links():
    return tuple(
        AtlasLink(
            atlas_type=atlas_type,
            atlas_record_id=(
                f"ATLAS-{index:02d}"
            ),
        )
        for index, atlas_type in enumerate(
            sorted(RCAF_ATLAS_TYPES),
            start=1,
        )
    )


def _record():
    return RCAFOrganizationalRecord(
        record_id="RCAF-ORG-0001",
        event_id="EVENT-0001",
        lineage_id="LINEAGE-0001",
        occurred_at_utc=(
            "2026-07-31T22:00:00Z"
        ),
        record_type="composite",
        stage="edge_admissibility",
        atlas_links=_links(),
        reference=ReferenceGeometry(
            reference_id="REF-0001",
            reference_class_id="REFCLASS-0001",
            anchor_id="ANCHOR-0001",
            drift=MetricVector(
                (
                    ("temporal", 0.1),
                    ("structural", 0.2),
                )
            ),
            deviation=MetricVector(
                (
                    ("local", 0.05),
                )
            ),
            future_freedom=MetricVector(
                (
                    ("optionality", 0.8),
                    ("reversibility", 0.9),
                )
            ),
        ),
        participation=ParticipationGeometry(
            pi_i=0.6,
            pi_r=0.3,
            pi_a=0.1,
            strain=0.4,
        ),
        coherence_geometry=CoherenceGeometry(
            meta_field_id="META-0001",
            psi=0.72,
            rho_a=0.18,
            rho_c=0.64,
            occupancy=0.55,
            geometry_coherence=0.81,
            realization_pressure=0.35,
            viability=0.88,
            realization_coupling=0.42,
        ),
        evidence=EvidenceGeometry(
            status="verified",
            evidence_ids=(
                "EVIDENCE-0001",
                "EVIDENCE-0002",
            ),
            benefit=MetricVector(
                (
                    ("immediate", 0.4),
                    ("far_field", 0.2),
                )
            ),
            harm=MetricVector(
                (
                    ("observed", 0.0),
                )
            ),
            containment=MetricVector(
                (
                    ("bounded", 1.0),
                )
            ),
            reversibility=MetricVector(
                (
                    ("rollback", 1.0),
                )
            ),
            confidence=MetricVector(
                (
                    ("structural", 0.9),
                )
            ),
            future_freedom_delta=MetricVector(
                (
                    ("optionality", 0.05),
                )
            ),
        ),
        future_conditioning=FutureConditioning(
            horizon=8,
            terminal_contract_id=(
                "TERMINAL-CONTRACT-0001"
            ),
            terminal_class_id=(
                "TERMINAL-CLASS-0001"
            ),
            corridor_id="CORRIDOR-0001",
            forward_reachable=True,
            backward_consistent=True,
            gate_admissible=True,
        ),
        boundary_authority=BoundaryAuthority(
            boundary_state="bounded",
            admissibility_state="admissible",
            authority_state="none",
            scope_ids=(
                "SCOPE-OBSERVE",
            ),
            release_condition_ids=(
                "RELEASE-ON-COMPLETE",
            ),
            revocation_condition_ids=(
                "REVOKE-ON-BOUNDARY-BREACH",
            ),
            realization_authorized=False,
        ),
        lifecycle=RealizationLifecycle(
            possibility="possible",
            proposal="proposed",
            verification="verified",
            transition_qualification="qualified",
            edge_admissibility="admissible",
            geometry_coherence="verified",
            active_participation_permission=(
                "not_observed"
            ),
            realization="not_observed",
            absorption="not_observed",
            influence="not_observed",
            persistence="not_observed",
            release="not_observed",
        ),
        causal_flow=CausalFlow(
            interface_id="INTERFACE-0001",
            coupling_id="COUPLING-0001",
            propagation_path_id=(
                "PROPAGATION-0001"
            ),
            scale_transition_id=(
                "SCALE-0001"
            ),
            global_influence_id=(
                "GLOBAL-0001"
            ),
            coupling_strength=MetricVector(
                (
                    ("local", 0.4),
                )
            ),
            propagation_strength=MetricVector(
                (
                    ("contained", 0.9),
                )
            ),
        ),
        counterfactual=CounterfactualComparison(
            status="complete",
            candidate_ids=(
                "CANDIDATE-A",
                "CANDIDATE-B",
            ),
            selected_candidate_id=(
                "CANDIDATE-A"
            ),
            comparison_metrics=MetricVector(
                (
                    ("benefit_delta", 0.2),
                    ("harm_delta", -0.1),
                )
            ),
        ),
        authority_posture="observe_only",
    )


def test_full_record_preserves_all_atlas_types():
    record = _record()
    summary = record.structural_summary()

    assert summary["atlas_count"] == len(
        RCAF_ATLAS_TYPES
    )
    assert set(
        summary["atlas_types"]
    ) == RCAF_ATLAS_TYPES
    assert summary["section_count"] == 9


def test_participation_remains_triadic():
    record = _record()
    participation = record.to_dict()[
        "participation"
    ]

    assert participation == {
        "pi_i": 0.6,
        "pi_r": 0.3,
        "pi_a": 0.1,
        "strain": 0.4,
    }


def test_future_conditioned_gate_is_explicit():
    future = _record().to_dict()[
        "future_conditioning"
    ]

    assert future["forward_reachable"] is True
    assert future["backward_consistent"] is True
    assert future["gate_admissible"] is True
    assert future["corridor_id"] == (
        "CORRIDOR-0001"
    )


def test_authority_is_not_collapsed_into_success():
    record = _record().to_dict()

    assert record[
        "boundary_authority"
    ]["authority_state"] == "none"

    assert record[
        "boundary_authority"
    ]["realization_authorized"] is False

    assert record[
        "lifecycle"
    ]["verification"] == "verified"

    assert record[
        "lifecycle"
    ]["realization"] == "not_observed"

    assert record[
        "lifecycle"
    ]["persistence"] == "not_observed"


def test_record_is_structural_and_private():
    marker = (
        "SR-PRIVATE-"
        "7f875da157e5a4eb0d2f6489b34585bc"
    )

    serialized = _record().canonical_json()

    assert marker not in serialized
    assert '"raw_content_stored":false' in serialized
    assert (
        '"content_fingerprint_stored":false'
        in serialized
    )


def test_canonical_serialization_is_deterministic():
    first = _record().canonical_json()
    second = _record().canonical_json()

    assert first == second

    parsed = json.loads(first)

    assert parsed["record_id"] == "RCAF-ORG-0001"


def test_invalid_participation_is_rejected():
    with pytest.raises(
        RCAFOrganizationalLedgerError,
        match="must equal 1",
    ):
        ParticipationGeometry(
            pi_i=0.7,
            pi_r=0.4,
            pi_a=0.1,
            strain=0.2,
        )


def test_unknown_atlas_type_is_rejected():
    with pytest.raises(
        RCAFOrganizationalLedgerError,
        match="atlas_type must be one of",
    ):
        AtlasLink(
            atlas_type="InventedAtlas",
            atlas_record_id="ATLAS-INVALID",
        )


def test_counterfactual_selection_must_be_candidate():
    with pytest.raises(
        RCAFOrganizationalLedgerError,
        match="must occur in candidate_ids",
    ):
        CounterfactualComparison(
            status="complete",
            candidate_ids=(
                "CANDIDATE-A",
            ),
            selected_candidate_id=(
                "CANDIDATE-B"
            ),
        )


def test_record_api_has_no_prompt_content_fields():
    signature = inspect.signature(
        RCAFOrganizationalRecord
    )

    forbidden = {
        "prompt",
        "response",
        "message",
        "content",
        "reasoning",
        "sha256",
        "embedding",
    }

    assert forbidden.isdisjoint(
        signature.parameters
    )
