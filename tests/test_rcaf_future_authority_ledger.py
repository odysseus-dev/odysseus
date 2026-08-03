# ============================================================
# tests/test_rcaf_future_authority_ledger.py
# ============================================================

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from src.rcaf.future_authority_ledger import (
    MATCHED_CAUSAL_BRANCH_ROLES,
    AntiCoercionAuthorityCheck,
    BackwardPredecessorEvidence,
    BidirectionalConsistencyRecord,
    BidirectionalMetaFieldReference,
    ForwardReachabilityEvidence,
    ForwardTrajectoryBranch,
    FutureAuthorityLedgerError,
    FutureConditionedAuthorityEvidenceBundle,
    FutureConditionedGateLifecycle,
    FutureGateCalibration,
    MatchedCausalBranch,
    MatchedCausalExperiment,
    ProspectiveGateNomination,
    RetrospectivePrecursorAtlasRecord,
    TerminalOrganizationalContract,
)

from src.rcaf.organizational_ledger import (
    MetricVector,
)


def _vector(
    name: str,
    value: float = 0.5,
) -> MetricVector:
    return MetricVector(
        (
            (name, value),
        )
    )


def _matched_branch(
    role: str,
) -> MatchedCausalBranch:
    relation_by_role = {
        "A0_base_preserve": "within_boundary",
        "A1_valid_candidate": "within_boundary",
        "A2_sham": "sham",
        "A3_wrong_context": "wrong_context",
        "A4_over_boundary": "over_boundary",
        "A5_support_withdrawal": "support_withdrawal",
    }

    return MatchedCausalBranch(
        branch_id=f"BRANCH-{role}",
        branch_role=role,
        shared_initial_state_contract_id="INITIAL-STATE-0001",
        intervention_id=f"INTERVENTION-{role}",
        context_id=f"CONTEXT-{role}",
        boundary_relation=relation_by_role[role],
        terminal_class_id=(
            "TERMINAL-CLASS-0001"
            if role
            in {
                "A1_valid_candidate",
                "A5_support_withdrawal",
            }
            else "TERMINAL-CLASS-CONTROL"
        ),
        evaluator_id="EVALUATOR-INDEPENDENT-0001",
        support_present=(
            role != "A5_support_withdrawal"
        ),
        execution_performed=True,
        verification_state="verified",
        benefit=_vector("benefit"),
        harm=_vector("harm", 0.0),
        containment=_vector("containment", 1.0),
        future_freedom=_vector("future_freedom", 0.8),
        absorption=_vector("absorption", 0.7),
        persistence=_vector("persistence", 0.7),
        release=_vector("release", 0.9),
    )


def _bundle() -> FutureConditionedAuthorityEvidenceBundle:
    terminal_contract = TerminalOrganizationalContract(
        contract_id="FUTURE-CONTRACT-0001",
        terminal_class_id="TERMINAL-CLASS-0001",
        benefit_requirements=_vector("benefit_minimum"),
        persistence_requirements=_vector(
            "persistence_minimum"
        ),
        harm_bounds=_vector("harm_maximum", 0.1),
        containment_requirements=_vector(
            "containment_minimum",
            0.9,
        ),
        absorption_requirements=_vector(
            "absorption_minimum",
            0.6,
        ),
        future_freedom_minima=_vector(
            "future_freedom_minimum",
            0.7,
        ),
        continuity_constraint_ids=(
            "CONTINUITY-ORGANIZATIONAL",
        ),
        release_condition_ids=(
            "RELEASE-AVAILABLE",
        ),
        rollback_mechanism_ids=(
            "ROLLBACK-AVAILABLE",
        ),
        allowed_variation_ids=(
            "VARIATION-MICROSTATE",
        ),
        forbidden_condition_ids=(
            "FORBID-AUTHORITY-EXPANSION",
        ),
    )

    forward_evidence = ForwardReachabilityEvidence(
        forward_map_id="META-T-PLUS-0001",
        reachable_set_id="REACHABLE-SET-0001",
        current_state_id="STATE-CURRENT-0001",
        candidate_action_id="ACTION-CANDIDATE-0001",
        branches=(
            ForwardTrajectoryBranch(
                branch_id="FORWARD-BRANCH-0001",
                seed_id="SEED-0001",
                carrier_state_id="CARRIER-STATE-0001",
                hidden_context_class_id="HIDDEN-CONTEXT-0001",
                disturbance_class_id="DISTURBANCE-0001",
                terminal_class_id="TERMINAL-CLASS-0001",
                corridor_id="ADMISSIBLE-CORRIDOR-0001",
                safe_corridor_occupied=True,
                boundary_compliant=True,
                release_available=True,
                future_freedom=_vector(
                    "future_freedom",
                    0.8,
                ),
                outcome_metrics=_vector("outcome", 0.7),
            ),
            ForwardTrajectoryBranch(
                branch_id="FORWARD-BRANCH-0002",
                seed_id="SEED-0002",
                carrier_state_id="CARRIER-STATE-0002",
                hidden_context_class_id="HIDDEN-CONTEXT-0002",
                disturbance_class_id="DISTURBANCE-0002",
                terminal_class_id="TERMINAL-CLASS-0001",
                corridor_id="ADMISSIBLE-CORRIDOR-0001",
                safe_corridor_occupied=True,
                boundary_compliant=True,
                release_available=True,
                future_freedom=_vector(
                    "future_freedom",
                    0.75,
                ),
                outcome_metrics=_vector("outcome", 0.65),
            ),
        ),
        reachable_terminal_class_ids=(
            "TERMINAL-CLASS-0001",
        ),
        safe_corridor_ids=("ADMISSIBLE-CORRIDOR-0001",),
        evidence_ids=("EVIDENCE-FORWARD-0001",),
        forward_reachable=True,
    )

    required_conditions = (
        "CONDITION-REFERENCE-VALID",
        "CONDITION-EVALUATOR-INDEPENDENT",
        "CONDITION-ROLLBACK-AVAILABLE",
        "CONDITION-RELEASE-AVAILABLE",
    )

    backward_evidence = BackwardPredecessorEvidence(
        backward_map_id="META-T-MINUS-0001",
        predecessor_set_id="PREDECESSOR-SET-0001",
        future_contract_id=terminal_contract.contract_id,
        required_reference_contract_id=(
            "REFERENCE-CONTRACT-0001"
        ),
        required_condition_ids=required_conditions,
        satisfied_condition_ids=required_conditions,
        unmet_condition_ids=(),
        forbidden_predecessor_condition_ids=(
            "FORBID-EVALUATOR-REWRITE",
        ),
        required_carrier_relation_ids=(
            "CARRIER-RELATION-BOUNDED",
        ),
        required_boundary_ids=("BOUNDARY-0001",),
        required_evaluator_ids=(
            "EVALUATOR-INDEPENDENT-0001",
        ),
        required_release_path_ids=(
            "RELEASE-PATH-0001",
        ),
        required_rollback_mechanism_ids=(
            "ROLLBACK-AVAILABLE",
        ),
        uncertainty=_vector("uncertainty", 0.1),
        backward_consistent=True,
    )

    consistency = BidirectionalConsistencyRecord(
        consistency_record_id="CONSISTENCY-0001",
        forward_reachable_set_id=(
            forward_evidence.reachable_set_id
        ),
        backward_predecessor_set_id=(
            backward_evidence.predecessor_set_id
        ),
        shared_corridor_ids=("ADMISSIBLE-CORRIDOR-0001",),
        excluded_trajectory_ids=(
            "TRAJECTORY-OVER-BOUNDARY",
        ),
        consistency_evidence_ids=(
            "EVIDENCE-CONSISTENCY-0001",
        ),
        consistency_failure_ids=(),
        terminal_class_agreement=True,
        intermediate_boundary_compliance=True,
        future_freedom_compliance=True,
        release_compliance=True,
        gate_nominated=True,
        gate_admissible=True,
    )

    causal_experiment = MatchedCausalExperiment(
        experiment_id="CAUSAL-EXPERIMENT-0001",
        frozen_future_contract_id=terminal_contract.contract_id,
        frozen_acceptance_criteria_id=(
            "ACCEPTANCE-CRITERIA-0001"
        ),
        independent_evaluator_id=(
            "EVALUATOR-INDEPENDENT-0001"
        ),
        branches=tuple(
            _matched_branch(role)
            for role in sorted(
                MATCHED_CAUSAL_BRANCH_ROLES
            )
        ),
        treatment_effects=_vector("treatment_effect", 0.3),
        control_separation=_vector(
            "control_separation",
            0.3,
        ),
        wrong_context_separation=_vector(
            "wrong_context_separation",
            0.4,
        ),
        over_boundary_failure=_vector(
            "over_boundary_failure",
            1.0,
        ),
        support_withdrawal_persistence=_vector(
            "withdrawal_persistence",
            0.8,
        ),
        cross_seed_robustness=_vector(
            "cross_seed",
            0.8,
        ),
        cross_carrier_robustness=_vector(
            "cross_carrier",
            0.7,
        ),
        evaluator_agreement=_vector(
            "evaluator_agreement",
            0.9,
        ),
        valid_treatment_preferential=True,
        controls_weaker=True,
        withdrawal_persistent=True,
        independent_verification_passed=True,
        causal_support_established=True,
    )

    anti_coercion = AntiCoercionAuthorityCheck(
        check_id="ANTI-COERCION-0001",
        prediction_preceded_intervention=True,
        terminal_contract_frozen=True,
        acceptance_criteria_frozen=True,
        evaluator_independent=True,
        target_rewrite_prevented=True,
        evidence_rewrite_prevented=True,
        control_branches_preserved=True,
        valid_treatment_preferential=True,
        support_withdrawal_persistent=True,
        passed=True,
    )

    retrospective = RetrospectivePrecursorAtlasRecord(
        atlas_record_id="RETROSPECTIVE-ATLAS-0001",
        completed_trajectory_id="TRAJECTORY-COMPLETE-0001",
        terminal_class_id="TERMINAL-CLASS-0001",
        earliest_reliable_precursor_id="PRECURSOR-0001",
        precursor_class_id="PRECURSOR-CLASS-0001",
        decisive_carrier_coalition_ids=(
            "CARRIER-COALITION-0001",
        ),
        boundary_crossing_event_id="EVENT-BOUNDARY-0001",
        alternative_future_collapse_event_id=(
            "EVENT-FUTURE-COLLAPSE-0001"
        ),
        absorption_likelihood_event_id=(
            "EVENT-ABSORPTION-0001"
        ),
        release_danger_event_id="EVENT-RELEASE-DANGER-0001",
        inference_method_id="METHOD-SMOOTHING-0001",
        validation_population_id="POPULATION-0001",
        replication_evidence_ids=(
            "EVIDENCE-REPLICATION-0001",
        ),
        replicated=True,
    )

    prospective = ProspectiveGateNomination(
        nomination_id="NOMINATION-0001",
        observed_precursor_class_id=(
            retrospective.precursor_class_id
        ),
        matched_atlas_invariant_id=(
            "ATLAS-INVARIANT-0001"
        ),
        reference_contract_id="REFERENCE-CONTRACT-0001",
        corridor_match_id="ADMISSIBLE-CORRIDOR-0001",
        deviation_metrics=_vector("deviation", 0.1),
        evidence_ids=("EVIDENCE-NOMINATION-0001",),
        recommendation="sandbox_candidate",
    )

    calibration = FutureGateCalibration(
        calibration_id="CALIBRATION-0001",
        status="uncalibrated",
        future_class_confidence=None,
        future_basin_pull="observed",
        sample_count=12,
        effective_sample_count=8,
        calibration_population_id="POPULATION-0001",
        calibration_error=None,
        out_of_distribution=False,
        scenario_tail_metrics=_vector(
            "tail_risk",
            0.2,
        ),
    )

    lifecycle = FutureConditionedGateLifecycle(
        lifecycle_id="GATE-LIFECYCLE-0001",
        evidence_maturity="causally_supported",
        authority_status="observe_only",
        retrospective_atlas_record_id=(
            retrospective.atlas_record_id
        ),
        prospective_nomination_id=(
            prospective.nomination_id
        ),
        causal_experiment_id=causal_experiment.experiment_id,
        anti_coercion_check_id=anti_coercion.check_id,
        calibration_id=calibration.calibration_id,
    )

    meta_field = BidirectionalMetaFieldReference(
        meta_field_record_id="META-FIELD-0001",
        forward_transition_map_id=(
            forward_evidence.forward_map_id
        ),
        backward_predecessor_map_id=(
            backward_evidence.backward_map_id
        ),
        evidence_structure_id="EVIDENCE-STRUCTURE-0001",
        admissibility_authority_structure_id=(
            "AUTHORITY-STRUCTURE-0001"
        ),
        consistency_relation_id=(
            consistency.consistency_record_id
        ),
    )

    return FutureConditionedAuthorityEvidenceBundle(
        terminal_contract=terminal_contract,
        forward_evidence=forward_evidence,
        backward_evidence=backward_evidence,
        consistency=consistency,
        causal_experiment=causal_experiment,
        anti_coercion=anti_coercion,
        retrospective_atlas=retrospective,
        prospective_nomination=prospective,
        calibration=calibration,
        lifecycle=lifecycle,
        meta_field=meta_field,
    )


def test_bundle_preserves_bidirectional_evidence_lifecycle():
    bundle = _bundle()
    data = bundle.to_dict()

    assert data["terminal_contract"]["terminal_class_id"] == (
        "TERMINAL-CLASS-0001"
    )
    assert data["forward_evidence"]["forward_reachable"] is True
    assert data["backward_evidence"]["backward_consistent"] is True
    assert data["consistency"]["gate_admissible"] is True
    assert data["causal_experiment"][
        "causal_support_established"
    ] is True
    assert data["anti_coercion"]["passed"] is True


def test_matched_experiment_contains_exact_a0_through_a5():
    roles = {
        branch.branch_role
        for branch in _bundle().causal_experiment.branches
    }

    assert roles == MATCHED_CAUSAL_BRANCH_ROLES


def test_uncalibrated_evidence_does_not_claim_probability():
    calibration = _bundle().calibration

    assert calibration.status == "uncalibrated"
    assert calibration.future_class_confidence is None
    assert calibration.calibration_error is None
    assert not calibration.confidence_interval.components
    assert _bundle().causal_authority_eligible is False


def test_uncalibrated_confidence_is_rejected():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="uncalibrated evidence cannot claim confidence",
    ):
        replace(
            _bundle().calibration,
            future_class_confidence=0.8,
        )


def test_backward_consistency_rejects_unmet_condition():
    evidence = _bundle().backward_evidence

    with pytest.raises(
        FutureAuthorityLedgerError,
        match="cannot contain unmet conditions",
    ):
        replace(
            evidence,
            satisfied_condition_ids=(
                evidence.required_condition_ids[:-1]
            ),
            unmet_condition_ids=(
                evidence.required_condition_ids[-1],
            ),
            backward_consistent=True,
        )


def test_admissible_gate_rejects_consistency_failure():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="requires complete bidirectional consistency",
    ):
        replace(
            _bundle().consistency,
            consistency_failure_ids=(
                "FAILURE-CONSISTENCY-0001",
            ),
            gate_admissible=True,
        )


def test_causal_support_requires_all_controls():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="causal support requires",
    ):
        replace(
            _bundle().causal_experiment,
            controls_weaker=False,
            causal_support_established=True,
        )


def test_anti_coercion_result_cannot_be_self_declared():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="must equal all safeguards",
    ):
        replace(
            _bundle().anti_coercion,
            evaluator_independent=False,
            passed=True,
        )


def test_retrospective_atlas_cannot_grant_authority():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="cannot grant authority",
    ):
        replace(
            _bundle().retrospective_atlas,
            authority_granted=True,
        )


def test_prospective_nomination_must_withhold_authority():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="must withhold authority",
    ):
        replace(
            _bundle().prospective_nomination,
            authority_withheld=False,
        )


def test_meta_field_rejects_literal_retrocausality():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="does not claim literal backward causation",
    ):
        replace(
            _bundle().meta_field,
            literal_retrocausality_claimed=True,
        )


def test_future_gate_evidence_cannot_auto_promote_authority():
    with pytest.raises(
        FutureAuthorityLedgerError,
        match="cannot auto-promote authority",
    ):
        replace(
            _bundle().lifecycle,
            no_auto_promotion=False,
        )


def test_serialization_is_private_and_deterministic():
    marker = (
        "RCAF-FUTURE-PRIVATE-"
        "027c0db83a924e3b8e11242068f13d91"
    )

    first = _bundle().canonical_json()
    second = _bundle().canonical_json()

    assert first == second
    assert marker not in first
    assert '"raw_content_stored":false' in first
    assert '"content_fingerprint_stored":false' in first
    assert '"causal_authority_eligible":false' in first


def test_bundle_api_accepts_no_prompt_or_response_content():
    signature = inspect.signature(
        FutureConditionedAuthorityEvidenceBundle
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



def test_bundle_rejects_prospective_corridor_mismatch():
    bundle = _bundle()

    with pytest.raises(
        FutureAuthorityLedgerError,
        match="prospective nomination corridor mismatch",
    ):
        replace(
            bundle,
            prospective_nomination=replace(
                bundle.prospective_nomination,
                corridor_match_id="CORRIDOR-MISMATCH",
            ),
        )


def test_bundle_rejects_evaluator_linkage_mismatch():
    bundle = _bundle()

    with pytest.raises(
        FutureAuthorityLedgerError,
        match="independent evaluator is not a required predecessor",
    ):
        replace(
            bundle,
            causal_experiment=replace(
                bundle.causal_experiment,
                independent_evaluator_id=(
                    "EVALUATOR-MISMATCH"
                ),
            ),
        )
