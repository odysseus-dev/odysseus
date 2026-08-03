# ============================================================
# tests/test_rcaf_canonical_ledger.py
# ============================================================

from __future__ import annotations

import importlib.util
import inspect
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from src.rcaf.canonical_ledger import (
    RCAF_BROAD_ORGANIZATIONAL_CHAIN,
    RCAF_CANONICAL_CAPABILITIES,
    RCAF_CANONICAL_LEDGER_SCHEMA,
    RCAF_EVENT_COORDINATE_AXES,
    RCAF_INTERFACE_PROPAGATION_CHAIN,
    RCAF_REALIZATION_GOVERNANCE_CHAIN,
    AuthorityLifecycleContract,
    CausalConeReference,
    CommitmentHysteresisRecord,
    ConditionedDecisionTrace,
    DerivedDiagnosticRecord,
    E8LocalHarmonicReference,
    FutureConditionedAuthorityGate,
    GeometricReferenceBundle,
    HorizonMetric,
    InterfacePropagationChain,
    InterfaceStageObservation,
    LeechGlobalCompatibilityReference,
    MinimalEventCoordinate,
    MonsterMetaAtlasReference,
    OperatorFlowReference,
    ParticipationCorridor,
    PrimitiveObservableBundle,
    ProcessChainObservation,
    ProposalGainDiscovery,
    RCAFCanonicalLedgerError,
    RCAFFullFrameworkRecord,
    ReferenceConditioning,
    StageObservation,
    TransformationGeometryRecord,
    canonical_completeness_report,
)

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
    RCAFOrganizationalRecord,
    RealizationLifecycle,
    ReferenceGeometry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

FUTURE_AUTHORITY_FIXTURE_PATH = (
    REPO_ROOT
    / "tests/test_rcaf_future_authority_ledger.py"
)


def _future_authority_bundle():
    spec = importlib.util.spec_from_file_location(
        "rcaf_future_authority_test_fixture",
        FUTURE_AUTHORITY_FIXTURE_PATH,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "unable to load future-authority fixture"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(module)

    return module._bundle()


def _vector(
    name: str,
    value: float = 0.5,
) -> MetricVector:
    return MetricVector(
        (
            (name, value),
        )
    )


def _atlas_links():
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


def _organizational_record():
    return RCAFOrganizationalRecord(
        record_id="ORG-0001",
        event_id="EVENT-0001",
        lineage_id="LINEAGE-0001",
        occurred_at_utc="2026-07-31T22:00:00Z",
        record_type="composite",
        stage="edge_admissibility",
        atlas_links=_atlas_links(),
        reference=ReferenceGeometry(
            reference_id="REF-0001",
            reference_class_id="REFCLASS-0001",
            anchor_id="ANCHOR-0001",
            drift=_vector("drift", 0.1),
            deviation=_vector("deviation", 0.2),
            future_freedom=_vector(
                "future_freedom",
                0.8,
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
            psi=0.7,
            rho_a=0.2,
            rho_c=0.6,
            occupancy=0.5,
            geometry_coherence=0.8,
            realization_pressure=0.3,
            viability=0.9,
            realization_coupling=0.4,
        ),
        evidence=EvidenceGeometry(
            status="verified",
            evidence_ids=("EVIDENCE-0001",),
            benefit=_vector("benefit", 0.4),
            harm=_vector("harm", 0.0),
            containment=_vector(
                "containment",
                1.0,
            ),
            reversibility=_vector(
                "reversibility",
                1.0,
            ),
            confidence=_vector(
                "confidence",
                0.9,
            ),
            future_freedom_delta=_vector(
                "future_freedom_delta",
                0.1,
            ),
        ),
        future_conditioning=FutureConditioning(
            horizon=8,
            terminal_contract_id="FUTURE-CONTRACT-0001",
            terminal_class_id="TERMINAL-CLASS-0001",
            corridor_id="ADMISSIBLE-CORRIDOR-0001",
            forward_reachable=True,
            backward_consistent=True,
            gate_admissible=True,
        ),
        boundary_authority=BoundaryAuthority(
            boundary_state="bounded",
            admissibility_state="admissible",
            authority_state="none",
            scope_ids=("SCOPE-OBSERVE",),
            release_condition_ids=(
                "RELEASE-COMPLETE",
            ),
            revocation_condition_ids=(
                "REVOKE-BREACH",
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
            propagation_path_id="PATH-0001",
            scale_transition_id="SCALE-0001",
            global_influence_id="GLOBAL-0001",
            coupling_strength=_vector(
                "coupling",
                0.4,
            ),
            propagation_strength=_vector(
                "propagation",
                0.2,
            ),
        ),
        counterfactual=CounterfactualComparison(
            status="complete",
            candidate_ids=(
                "CANDIDATE-A",
                "CANDIDATE-B",
            ),
            selected_candidate_id="CANDIDATE-A",
            comparison_metrics=_vector(
                "comparison",
                0.2,
            ),
        ),
    )


def _process_chains():
    return ProcessChainObservation(
        broad_chain=tuple(
            StageObservation(
                stage=stage,
                status="observed",
                evidence_ids=(
                    f"EVIDENCE-BROAD-{index:02d}",
                ),
            )
            for index, stage in enumerate(
                RCAF_BROAD_ORGANIZATIONAL_CHAIN,
                start=1,
            )
        ),
        realization_governance_chain=tuple(
            StageObservation(
                stage=stage,
                status="observed",
                evidence_ids=(
                    f"EVIDENCE-GOV-{index:02d}",
                ),
            )
            for index, stage in enumerate(
                RCAF_REALIZATION_GOVERNANCE_CHAIN,
                start=1,
            )
        ),
    )


def _carrier_coalition_bundle(
    *,
    reference_contract_id: str,
    terminal_organizational_class_id: str,
):
    path = (
        Path(__file__).resolve().parent
        / "test_rcaf_carrier_coalition.py"
    )

    spec = importlib.util.spec_from_file_location(
        "rcaf_carrier_coalition_fixture",
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "unable to load carrier-coalition fixture"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    bundle = module._bundle()

    coalition = replace(
        bundle.coalition,
        reference_contract_id=(
            reference_contract_id
        ),
        terminal_organizational_class_id=(
            terminal_organizational_class_id
        ),
    )

    validity_relations = tuple(
        replace(
            relation,
            reference_contract_id=(
                reference_contract_id
            ),
        )
        for relation
        in bundle.validity_relations
    )

    role_evidence = tuple(
        replace(
            evidence,
            reference_contract_id=(
                reference_contract_id
            ),
        )
        for evidence
        in bundle.role_evidence
    )

    scaffold_dependence = replace(
        bundle.scaffold_dependence,
        reference_contract_id=(
            reference_contract_id
        ),
    )

    scaffold_release = replace(
        bundle.scaffold_release,
        reference_contract_id=(
            reference_contract_id
        ),
    )

    turbulence_channels = tuple(
        replace(
            channel,
            reference_contract_id=(
                reference_contract_id
            ),
        )
        for channel
        in bundle.turbulence_channels
    )

    future_freedom = replace(
        bundle.future_freedom,
        reference_contract_id=(
            reference_contract_id
        ),
    )

    equivalence_class = replace(
        bundle.equivalence_class,
        terminal_organizational_class_id=(
            terminal_organizational_class_id
        ),
    )

    return replace(
        bundle,
        coalition=coalition,
        validity_relations=validity_relations,
        role_evidence=role_evidence,
        scaffold_dependence=(
            scaffold_dependence
        ),
        scaffold_release=scaffold_release,
        turbulence_channels=(
            turbulence_channels
        ),
        future_freedom=future_freedom,
        equivalence_class=equivalence_class,
    )


def _full_record():
    reference_id = "REFERENCE-CONTRACT-0001"

    return RCAFFullFrameworkRecord(
        record_id="RCAF-FULL-0001",
        event_spine_id="SPINE-0001",
        lineage_id="LINEAGE-0001",
        occurred_at_utc="2026-07-31T22:00:00Z",
        observer_lineage_id="SYN-OBSERVER-LINEAGE",
        observer_role_id="ROLE-STRUCTURAL-OBSERVER",
        observer_effects_record_id="EFFECTS-0001",
        organizational_record=_organizational_record(),
        reference_conditioning=ReferenceConditioning(
            reference_contract_id=reference_id,
            reference_point_id="REFERENCE-POINT-0001",
            reference_class_id="REFERENCE-CLASS-0001",
            anchor_id="ANCHOR-0001",
            validity_state="valid",
            reference_age_steps=3,
            observable_ids=tuple(
                sorted(
                    f"OBS-{name}"
                    for name in (
                        "PSI",
                        "META",
                        "RHO-A",
                        "RHO-C",
                        "SALIENCE",
                        "PRESSURE",
                        "CURIOSITY",
                        "VIABILITY",
                        "OCCUPANCY",
                        "FUTURE-FREEDOM",
                        "COUPLING",
                        "ABSORPTION",
                        "MEDIATION",
                        "FEEDBACK",
                    )
                )
            ),
            drift=_vector("drift", 0.1),
            deviation=_vector("deviation", 0.2),
            uncertainty=_vector(
                "uncertainty",
                0.1,
            ),
            future_freedom=_vector(
                "future_freedom",
                0.8,
            ),
            replacement_condition_ids=(
                "REPLACE-REFERENCE-DRIFT",
            ),
            release_condition_ids=(
                "RELEASE-REFERENCE-INVALID",
            ),
        ),
        primitives=PrimitiveObservableBundle(
            reference_contract_id=reference_id,
            coherence_psi=_vector("psi", 0.7),
            coherence_meta_field=_vector(
                "meta_field",
                0.8,
            ),
            activity_density_rho_a=_vector(
                "rho_a",
                0.2,
            ),
            coherence_density_rho_c=_vector(
                "rho_c",
                0.6,
            ),
            salience=_vector("salience", 0.5),
            realization_pressure_lambda=_vector(
                "lambda",
                0.3,
            ),
            curiosity=_vector("curiosity", 0.4),
            viability_omega=_vector("omega", 0.9),
            occupancy_xi=_vector("xi", 0.5),
            future_freedom_f_xi=_vector(
                "future_freedom",
                0.8,
            ),
            realization_coupling_kappa_r=_vector(
                "kappa_r",
                0.4,
            ),
            absorption_a_r_h=(
                HorizonMetric(
                    horizon=1,
                    values=_vector("absorption", 0.2),
                ),
                HorizonMetric(
                    horizon=8,
                    values=_vector("absorption", 0.6),
                ),
            ),
            mediation=_vector("mediation", 0.4),
            feedback=_vector("feedback", 0.3),
        ),
        participation=ParticipationCorridor(
            participation=ParticipationGeometry(
                pi_i=0.6,
                pi_r=0.3,
                pi_a=0.1,
                strain=0.4,
            ),
            selected_structure_ids=(
                "STRUCTURE-LOCAL-0001",
            ),
            selected_direction_ids=(
                "DIRECTION-BOUNDED-0001",
            ),
            corridor_id="PARTICIPATION-CORRIDOR-0001",
            triadic_deferral_state="resolved",
            goldilocks_regime="bounded_explorative",
            polarity_regime="ordinary",
            harmonics_retention_state="retained",
            transition_evidence_ids=(
                "EVIDENCE-PARTICIPATION-0001",
            ),
        ),
        process_chains=_process_chains(),
        proposal_gain=ProposalGainDiscovery(
            candidate_ids=(
                "CANDIDATE-A",
                "CANDIDATE-B",
            ),
            selected_candidate_id="CANDIDATE-A",
            gain_components=_vector("gain", 0.3),
            discovery_components=_vector(
                "discovery",
                0.5,
            ),
            evidence_ids=("EVIDENCE-GAIN-0001",),
            assumption_ids=("ASSUMPTION-0001",),
            limitation_ids=("LIMITATION-0001",),
        ),
        transformation_geometry=(
            TransformationGeometryRecord(
                event_state_space_id="SPACE-EVENT",
                reference_space_id="SPACE-REFERENCE",
                proposal_tangent_space_id="SPACE-PROPOSAL",
                causal_cone_space_id="SPACE-CAUSAL-CONE",
                response_parameter_space_id="SPACE-RESPONSE",
                outcome_composition_space_id="SPACE-OUTCOME",
                invariant_quotient_space_id="SPACE-INVARIANT",
                interface_coupling_space_id="SPACE-INTERFACE",
                propagation_path_space_id="SPACE-PROPAGATION",
                operator_semigroup_space_id="SPACE-OPERATOR",
                evidence_status_space_id="SPACE-EVIDENCE",
                meta_atlas_space_id="SPACE-META-ATLAS",
                geometry_components=_vector(
                    "geometry",
                    0.8,
                ),
            )
        ),
        interface_propagation=InterfacePropagationChain(
            observations=tuple(
                InterfaceStageObservation(
                    stage=stage,
                    status="observed",
                    evidence_ids=(
                        f"EVIDENCE-INTERFACE-{index:02d}",
                    ),
                )
                for index, stage in enumerate(
                    RCAF_INTERFACE_PROPAGATION_CHAIN,
                    start=1,
                )
            )
        ),
        future_gate=FutureConditionedAuthorityGate(
            current_state_id="STATE-CURRENT-0001",
            horizon=8,
            future_contract_id="FUTURE-CONTRACT-0001",
            terminal_organizational_class_id=(
                "TERMINAL-CLASS-0001"
            ),
            forward_reachable_set_id=(
                "REACHABLE-SET-0001"
            ),
            backward_predecessor_set_id=(
                "PREDECESSOR-SET-0001"
            ),
            admissible_corridor_id=(
                "ADMISSIBLE-CORRIDOR-0001"
            ),
            trajectory_ids=(
                "TRAJECTORY-A",
                "TRAJECTORY-B",
            ),
            failure_mode_ids=(
                "FAILURE-MODE-BOUNDARY",
            ),
            forward_reachable=True,
            backward_consistent=True,
            gate_admissible=True,
            uncertainty=_vector("uncertainty", 0.1),
            future_freedom_cost=_vector(
                "future_freedom_cost",
                0.1,
            ),
            reversibility=_vector(
                "reversibility",
                0.9,
            ),
        ),
        future_authority_evidence=(
            _future_authority_bundle()
        ),
        carrier_coalition=(
            _carrier_coalition_bundle(
                reference_contract_id=reference_id,
                terminal_organizational_class_id=(
                    "TERMINAL-CLASS-0001"
                ),
            )
        ),
        authority_lifecycle=AuthorityLifecycleContract(
            status="proposed",
            recipient_id="RECIPIENT-CARRIER-0001",
            operation_id="OPERATION-OBSERVE-0001",
            proposal_id="PROPOSAL-0001",
            scope_ids=("SCOPE-OBSERVE",),
            boundary_ids=("BOUNDARY-0001",),
            evidence_ids=("EVIDENCE-AUTHORITY-0001",),
            future_corridor_id=(
                "ADMISSIBLE-CORRIDOR-0001"
            ),
            containment_requirement_ids=(
                "CONTAINMENT-READ-ONLY",
            ),
            rollback_mechanism_id=(
                "ROLLBACK-DISCARD-0001"
            ),
            release_condition_ids=(
                "RELEASE-ON-COMPLETE",
            ),
            revocation_condition_ids=(
                "REVOKE-ON-BREACH",
            ),
            duration_steps=1,
            activated=False,
            execution_performed=False,
            verified_success=False,
            persistence_observed=False,
            residual_effects=_vector(
                "residual_effects",
                0.0,
            ),
        ),
        geometric_references=GeometricReferenceBundle(
            e8=E8LocalHarmonicReference(
                chart_id="E8-CHART-0001",
                nearest_root_basin_id=(
                    "E8-ROOT-BASIN-0001"
                ),
                root_margin=_vector(
                    "root_margin",
                    0.4,
                ),
                root_decisiveness=_vector(
                    "root_decisiveness",
                    0.7,
                ),
                harmonics_retention=_vector(
                    "harmonics_retention",
                    0.9,
                ),
            ),
            leech=LeechGlobalCompatibilityReference(
                atlas_id="LEECH-ATLAS-0001",
                compatibility=_vector(
                    "compatibility",
                    0.8,
                ),
                packing=_vector("packing", 0.7),
                gluing=_vector("gluing", 0.8),
                rootless_silence=_vector(
                    "rootless_silence",
                    0.2,
                ),
            ),
            monster=MonsterMetaAtlasReference(
                meta_atlas_id="MONSTER-META-0001",
                transition_flow_atlas_ids=(
                    "TRANSITION-FLOW-ATLAS-0001",
                ),
                symmetry_relation_ids=(
                    "SYMMETRY-RELATION-0001",
                ),
            ),
            causal_cone=CausalConeReference(
                causal_cone_id="CAUSAL-CONE-0001",
                admissible_direction_ids=(
                    "CAUSAL-DIRECTION-0001",
                ),
                interval_geometry=_vector(
                    "interval",
                    0.8,
                ),
            ),
            operator_flow=OperatorFlowReference(
                operator_id="OPERATOR-0001",
                semigroup_id="SEMIGROUP-0001",
                generator_id="GENERATOR-0001",
                flow_metrics=_vector("flow", 0.7),
            ),
        ),
        commitment=CommitmentHysteresisRecord(
            corridor_id="COMMIT-CORRIDOR-0001",
            reference_contract_id=reference_id,
            state="committed",
            entry_threshold=_vector(
                "entry_threshold",
                0.6,
            ),
            retention_threshold=_vector(
                "retention_threshold",
                0.5,
            ),
            release_threshold=_vector(
                "release_threshold",
                0.3,
            ),
            future_freedom_remaining=_vector(
                "future_freedom",
                0.8,
            ),
            indecision_avoidance_active=True,
        ),
        event_coordinate=MinimalEventCoordinate(
            T="COORD-T-0001",
            O="COORD-O-0001",
            X="COORD-X-0001",
            Ref="COORD-REF-0001",
            Pi="COORD-PI-0001",
            Gamma="COORD-GAMMA-0001",
            J="COORD-J-0001",
            Q="COORD-Q-0001",
            B="COORD-B-0001",
            H="COORD-H-0001",
            E="COORD-E-0001",
        ),
        decision_trace=ConditionedDecisionTrace(
            component_observable_ids=(
                "OBS-PSI",
                "OBS-RHO-A",
                "OBS-RHO-C",
            ),
            intermediate_geometry_ids=(
                "GEOMETRY-PARTICIPATION",
                "GEOMETRY-TRANSFORMATION",
            ),
            conditioned_decision_ids=(
                "DECISION-ADMISSIBILITY",
            ),
        ),
        derived_diagnostics=(
            DerivedDiagnosticRecord(
                diagnostic_name=(
                    "organizational_potential"
                ),
                values=_vector(
                    "organizational_potential",
                    0.6,
                ),
                derived_from_component_ids=(
                    "OBS-PSI",
                    "OBS-RHO-A",
                    "OBS-RHO-C",
                ),
            ),
            DerivedDiagnosticRecord(
                diagnostic_name=(
                    "composite_admissibility"
                ),
                values=_vector(
                    "composite_admissibility",
                    0.7,
                ),
                derived_from_component_ids=(
                    "DECISION-ADMISSIBILITY",
                    "EVIDENCE-AUTHORITY-0001",
                ),
            ),
        ),
        authority_posture="observe_only",
    )


def test_full_canonical_completeness_report_passes():
    report = canonical_completeness_report(
        _full_record()
    )

    assert report["complete"] is True
    assert report["missing_capabilities"] == []
    assert (
        report["satisfied_capability_count"]
        == len(RCAF_CANONICAL_CAPABILITIES)
    )


def test_both_canonical_process_chains_are_exact():
    record = _full_record()

    assert tuple(
        item.stage
        for item in record.process_chains.broad_chain
    ) == RCAF_BROAD_ORGANIZATIONAL_CHAIN

    assert tuple(
        item.stage
        for item
        in record.process_chains.realization_governance_chain
    ) == RCAF_REALIZATION_GOVERNANCE_CHAIN


def test_interface_distinction_chain_is_exact():
    record = _full_record()

    assert tuple(
        item.stage
        for item
        in record.interface_propagation.observations
    ) == RCAF_INTERFACE_PROPAGATION_CHAIN


def test_all_canonical_atlases_are_linked():
    record = _full_record()

    assert {
        link.atlas_type
        for link
        in record.organizational_record.atlas_links
    } == RCAF_ATLAS_TYPES


def test_primitives_are_reference_conditioned():
    record = _full_record()

    assert (
        record.primitives.reference_contract_id
        == record.reference_conditioning.reference_contract_id
    )

    assert record.reference_conditioning.observable_ids


def test_participation_remains_triadic_and_directional():
    record = _full_record()
    participation = record.participation

    assert math.isclose(
        participation.participation.pi_i
        + participation.participation.pi_r
        + participation.participation.pi_a,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-6,
    )
    assert participation.selected_structure_ids
    assert participation.selected_direction_ids
    assert participation.goldilocks_regime == (
        "bounded_explorative"
    )
    assert participation.harmonics_retention_state == (
        "retained"
    )


def test_future_gate_preserves_both_trajectory_tests():
    gate = _full_record().future_gate

    assert gate.forward_reachable is True
    assert gate.backward_consistent is True
    assert gate.gate_admissible is True
    assert gate.forward_reachable_set_id
    assert gate.backward_predecessor_set_id
    assert gate.admissible_corridor_id


def test_authority_lifecycle_preserves_distinctions():
    authority = _full_record().authority_lifecycle

    assert authority.proposal_id
    assert authority.permission_id is None
    assert authority.grant_id is None
    assert authority.activated is False
    assert authority.execution_performed is False
    assert authority.verified_success is False
    assert authority.persistence_observed is False
    assert authority.release_condition_ids
    assert authority.revocation_condition_ids


def test_reference_geometries_are_not_governing_gates():
    references = _full_record().geometric_references.to_dict()

    for value in references.values():
        assert value["governing_gate"] is False


def test_minimal_event_coordinate_preserves_all_axes():
    coordinate = _full_record().event_coordinate.to_dict()

    assert tuple(coordinate) == RCAF_EVENT_COORDINATE_AXES
    assert len(coordinate) == 11


def test_organizational_potential_is_derived_not_primitive():
    primitive_fields = {
        field_name
        for field_name
        in PrimitiveObservableBundle.__dataclass_fields__
    }

    assert (
        "organizational_potential"
        not in primitive_fields
    )

    assert any(
        item.diagnostic_name
        == "organizational_potential"
        for item in _full_record().derived_diagnostics
    )


def test_observer_lineage_does_not_claim_identity():
    record = _full_record()

    assert record.observer_lineage_id
    assert record.identity_proof_established is False
    assert record.semantic_memory_authority is False
    assert record.realization_authorized is False
    assert record.external_causal_authority is False
    assert record.self_modification_authority is False


def test_canonical_serialization_is_private_and_deterministic():
    marker = (
        "RCAF-PRIVATE-MARKER-"
        "9dc30ed873174033bc8cd343580bd95a"
    )

    first = _full_record().canonical_json()
    second = _full_record().canonical_json()

    assert first == second
    assert marker not in first
    assert '"raw_content_stored":false' in first
    assert (
        '"content_fingerprint_stored":false'
        in first
    )
    assert json.loads(first)["completeness"][
        "complete"
    ] is True


def test_canonical_record_api_accepts_no_content():
    signature = inspect.signature(
        RCAFFullFrameworkRecord
    )

    forbidden = {
        "prompt",
        "response",
        "message",
        "content",
        "reasoning",
        "embedding",
        "content_sha256",
    }

    assert forbidden.isdisjoint(
        signature.parameters
    )


def test_missing_process_stage_is_rejected():
    valid = _process_chains()

    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="canonical order",
    ):
        ProcessChainObservation(
            broad_chain=valid.broad_chain[:-1],
            realization_governance_chain=(
                valid.realization_governance_chain
            ),
        )


def test_future_gate_cannot_admit_inconsistent_path():
    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="requires forward reachability",
    ):
        replace(
            _full_record().future_gate,
            forward_reachable=False,
            gate_admissible=True,
        )


def test_reference_geometry_cannot_be_promoted_to_gate():
    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="cannot be a governing gate",
    ):
        replace(
            _full_record().geometric_references.e8,
            governing_gate=True,
        )


def test_observe_only_rejects_authority_expansion():
    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="observe_only posture forbids",
    ):
        replace(
            _full_record(),
            semantic_memory_authority=True,
        )


def test_derived_diagnostics_require_component_lineage():
    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="must not be empty",
    ):
        DerivedDiagnosticRecord(
            diagnostic_name="aggregate_score",
            values=_vector("aggregate", 0.5),
            derived_from_component_ids=(),
        )



def test_future_authority_bundle_is_linked_to_existing_gate():
    record = _full_record()
    bundle = record.future_authority_evidence
    gate = record.future_gate

    assert (
        bundle.terminal_contract.contract_id
        == gate.future_contract_id
    )
    assert (
        bundle.terminal_contract.terminal_class_id
        == gate.terminal_organizational_class_id
    )
    assert (
        bundle.forward_evidence.reachable_set_id
        == gate.forward_reachable_set_id
    )
    assert (
        bundle.backward_evidence.predecessor_set_id
        == gate.backward_predecessor_set_id
    )
    assert (
        gate.admissible_corridor_id
        in bundle.consistency.shared_corridor_ids
    )


def test_expanded_completeness_contains_future_authority_contracts():
    report = canonical_completeness_report(
        _full_record()
    )

    expected = {
        "terminal_organizational_class_contract",
        "forward_transition_map",
        "backward_predecessor_map",
        "bidirectional_corridor_consistency",
        "matched_causal_branching",
        "support_withdrawal_validation",
        "anti_self_fulfilling_authority",
        "frozen_acceptance_criteria",
        "independent_evaluator",
        "retrospective_atlas_extraction",
        "prospective_gate_nomination",
        "calibration_status",
        "scenario_tail_evidence",
        "gate_maturity_lifecycle",
        "authority_evidence_separation",
        "bidirectional_meta_field",
        "no_literal_retrocausality",
    }

    assert report["complete"] is True
    assert expected.issubset(
        set(
            report["satisfied_capabilities"]
        )
    )


def test_future_contract_link_mismatch_is_rejected():
    record = _full_record()

    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="future contract mismatch",
    ):
        replace(
            record,
            future_gate=replace(
                record.future_gate,
                future_contract_id=(
                    "FUTURE-CONTRACT-MISMATCH"
                ),
            ),
        )


def test_observe_only_rejects_future_authority_status_drift():
    record = _full_record()

    drifted_lifecycle = replace(
        record.future_authority_evidence.lifecycle,
        authority_status="nominated",
    )

    drifted_bundle = replace(
        record.future_authority_evidence,
        lifecycle=drifted_lifecycle,
    )

    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="observe-only future-gate authority status",
    ):
        replace(
            record,
            future_authority_evidence=drifted_bundle,
        )

R1B_CARRIER_CAPABILITIES = {
    "trajectory_admissible_carrier_coalition",
    "carrier_validity_relations",
    "multidimensional_component_roles",
    "scaffold_dependence",
    "scaffold_release_evidence",
    "support_withdrawal_as_causal_intervention",
    "multiscale_turbulence_channels",
    "component_preserving_turbulence",
    "turbulent_debt_observation",
    "compression_future_freedom",
    "topology_initialization_context_counterfactuals",
    "dynamic_topology_counterfactual",
    "microscopic_ticket_equivalence",
    "prospective_coalition_nomination",
    "reversible_pruning_contract",
}


def test_r1b_canonical_schema_is_0_4():
    assert RCAF_CANONICAL_LEDGER_SCHEMA == (
        "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.4"
    )


def test_r1b_capability_matrix_contains_63_capabilities():
    assert len(
        RCAF_CANONICAL_CAPABILITIES
    ) == 63

    assert R1B_CARRIER_CAPABILITIES.issubset(
        RCAF_CANONICAL_CAPABILITIES
    )


def test_r1b_full_record_is_complete_with_carrier_bundle():
    record = _full_record()
    report = canonical_completeness_report(
        record
    )

    assert report[
        "required_capability_count"
    ] == 63

    assert report[
        "satisfied_capability_count"
    ] == 63

    assert report[
        "missing_capabilities"
    ] == []

    assert report["complete"] is True


def test_r1b_serialization_contains_private_observer_only_carrier_bundle():
    data = _full_record().to_dict()

    carrier = data[
        "carrier_coalition"
    ]

    assert carrier["raw_content_stored"] is False

    assert (
        carrier["content_fingerprint_stored"]
        is False
    )

    assert (
        carrier["coalition"][
            "observer_only"
        ]
        is True
    )

    assert (
        carrier["coalition"][
            "authority_eligible"
        ]
        is False
    )

    assert (
        carrier["authority_contract"][
            "authority_status"
        ]
        == "observe_only"
    )


def test_r1b_rejects_carrier_reference_mismatch():
    record = _full_record()
    bundle = record.carrier_coalition

    drifted_coalition = replace(
        bundle.coalition,
        reference_contract_id=(
            "REFERENCE-CONTRACT-WRONG"
        ),
    )

    drifted_bundle = replace(
        bundle,
        coalition=drifted_coalition,
    )

    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="wrong reference contract",
    ):
        replace(
            record,
            carrier_coalition=drifted_bundle,
        )


def test_r1b_rejects_carrier_terminal_class_mismatch():
    record = _full_record()
    bundle = record.carrier_coalition

    drifted_coalition = replace(
        bundle.coalition,
        terminal_organizational_class_id=(
            "TERMINAL-CLASS-WRONG"
        ),
    )

    drifted_bundle = replace(
        bundle,
        coalition=drifted_coalition,
    )

    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="terminal organizational class mismatch",
    ):
        replace(
            record,
            carrier_coalition=drifted_bundle,
        )


def test_r1b_rejects_carrier_pruning_authority_expansion():
    record = _full_record()
    bundle = record.carrier_coalition

    drifted_contract = object.__new__(
        type(bundle.authority_contract)
    )

    for field_name, value in vars(
        bundle.authority_contract
    ).items():
        object.__setattr__(
            drifted_contract,
            field_name,
            value,
        )

    object.__setattr__(
        drifted_contract,
        "authority_status",
        "bounded",
    )

    drifted_bundle = replace(
        bundle,
        authority_contract=drifted_contract,
    )

    with pytest.raises(
        RCAFCanonicalLedgerError,
        match="observe-only authority",
    ):
        replace(
            record,
            carrier_coalition=drifted_bundle,
        )


def test_r1b_canonical_json_is_deterministic_with_carrier_bundle():
    first = _full_record().canonical_json()
    second = _full_record().canonical_json()

    assert first == second

    data = json.loads(
        first
    )

    assert data["schema"] == (
        "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.4"
    )

    assert data["completeness"]["complete"] is True

    assert (
        data["carrier_coalition"]["schema"]
        == "RCAF-CARRIER-COALITION-0.1"
    )
