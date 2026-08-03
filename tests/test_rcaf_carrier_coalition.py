# ============================================================
# tests/test_rcaf_carrier_coalition.py
# ============================================================

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.rcaf.carrier_coalition import (
    COMPONENT_ROLE_NAMES,
    COUNTERFACTUAL_ARMS,
    RCAF_CARRIER_COALITION_SCHEMA,
    TURBULENCE_COMPONENT_NAMES,
    CarrierCoalitionContractError,
    CarrierValidityRelation,
    CoalitionCounterfactualBranch,
    CoalitionMatchedExperiment,
    ComponentRoleEvidence,
    CompressionFutureFreedom,
    MicroscopicTicketEquivalenceClass,
    MultiscaleTurbulenceChannel,
    ProspectiveCoalitionNomination,
    RCAFCarrierCoalitionBundle,
    ReversiblePruningAuthorityContract,
    ScaffoldDependenceEvidence,
    ScaffoldReleaseEvidence,
    TrajectoryAdmissibleCarrierCoalition,
)


def _branches(
    coalition_id: str = "coalition-main",
) -> tuple[CoalitionCounterfactualBranch, ...]:
    return tuple(
        CoalitionCounterfactualBranch(
            branch_id=f"branch-{arm}",
            arm=arm,
            coalition_id=coalition_id,
            topology_relation=f"topology-{arm}",
            initialization_relation=f"initialization-{arm}",
            context_relation=f"context-{arm}",
            support_relation=f"support-{arm}",
            dynamic_topology=(
                arm == "A8"
            ),
            outcome_record_ids=(
                f"outcome-{arm}",
            ),
        )
        for arm in COUNTERFACTUAL_ARMS
    )


def _bundle() -> RCAFCarrierCoalitionBundle:
    coalition = TrajectoryAdmissibleCarrierCoalition(
        coalition_id="coalition-main",
        member_ids=(
            "member-payload",
            "member-router",
            "member-scaffold",
        ),
        initialization_state_id="initialization-001",
        optimizer_state_id="optimizer-001",
        data_contract_id="data-contract-001",
        reference_contract_id="reference-contract-001",
        participation_geometry_id="participation-001",
        transformation_geometry_id="transformation-001",
        boundary_contract_id="boundary-001",
        terminal_organizational_class_id="terminal-class-001",
        scaffold_relation_ids=(
            "relation-scaffold",
        ),
        turbulence_channel_ids=(
            "turbulence-001",
        ),
        role_evidence_ids=(
            "role-payload",
            "role-scaffold",
        ),
        future_freedom_record_id="future-freedom-001",
        horizon_steps=128,
        forward_reachable=True,
        backward_consistent=True,
        self_supporting=True,
        support_withdrawal_verified=True,
        context_specific=True,
    )

    validity_relations = (
        CarrierValidityRelation(
            relation_id="relation-scaffold",
            source_member_id="member-scaffold",
            target_member_id="member-payload",
            relation_kind="scaffold",
            reference_contract_id="reference-contract-001",
            evidence_record_ids=(
                "evidence-validity-001",
            ),
            context_validated=True,
            phase_compatible=True,
            boundary_compatible=True,
            future_consistent=True,
        ),
        CarrierValidityRelation(
            relation_id="relation-route",
            source_member_id="member-router",
            target_member_id="member-payload",
            relation_kind="route",
            reference_contract_id="reference-contract-001",
            evidence_record_ids=(
                "evidence-validity-002",
            ),
            context_validated=True,
            phase_compatible=True,
            boundary_compatible=True,
            future_consistent=True,
        ),
    )

    role_evidence = (
        ComponentRoleEvidence(
            evidence_id="role-payload",
            member_id="member-payload",
            reference_contract_id="reference-contract-001",
            horizon_steps=128,
            role_values=(
                0.95,
                0.15,
                0.40,
                0.20,
                0.30,
                0.10,
                0.25,
                0.05,
            ),
            evidence_record_ids=(
                "evidence-role-001",
            ),
        ),
        ComponentRoleEvidence(
            evidence_id="role-scaffold",
            member_id="member-scaffold",
            reference_contract_id="reference-contract-001",
            horizon_steps=128,
            role_values=(
                0.20,
                0.92,
                0.35,
                0.70,
                0.40,
                0.15,
                0.30,
                0.10,
            ),
            evidence_record_ids=(
                "evidence-role-002",
            ),
        ),
    )

    scaffold_dependence = ScaffoldDependenceEvidence(
        dependence_id="dependence-001",
        coalition_id="coalition-main",
        scaffold_member_ids=(
            "member-scaffold",
        ),
        reference_contract_id="reference-contract-001",
        observation_horizon=64,
        dependence_values=(
            0.10,
            0.12,
            0.08,
            0.20,
            0.06,
            0.10,
            0.08,
        ),
        support_required_now=False,
        evidence_record_ids=(
            "evidence-dependence-001",
        ),
    )

    scaffold_release = ScaffoldReleaseEvidence(
        release_id="release-001",
        coalition_id="coalition-main",
        scaffold_member_ids=(
            "member-scaffold",
        ),
        reference_contract_id="reference-contract-001",
        withdrawal_horizon=64,
        immediate_performance_preserved=True,
        far_horizon_performance_preserved=True,
        calibration_preserved=True,
        robustness_preserved=True,
        optimizer_stable=True,
        activation_geometry_stable=True,
        persistence_verified=True,
        recovery_available=True,
        transfer_preserved=True,
        future_freedom_preserved=True,
        turbulent_debt_admissible=True,
        evidence_record_ids=(
            "evidence-release-001",
        ),
    )

    turbulence = MultiscaleTurbulenceChannel(
        channel_id="turbulence-001",
        source_organization_id="member-scaffold",
        target_organization_id="coalition-main",
        reference_contract_id="reference-contract-001",
        horizon_steps=128,
        component_values=(
            0.10,
            0.12,
            0.08,
            0.06,
            0.11,
            0.07,
            0.05,
            0.03,
            0.06,
            0.08,
        ),
        turbulence_classes=(
            "productive_transition",
        ),
        evidence_record_ids=(
            "evidence-turbulence-001",
        ),
    )

    future_freedom = CompressionFutureFreedom(
        record_id="future-freedom-001",
        coalition_id="coalition-main",
        reference_contract_id="reference-contract-001",
        baseline_values=(
            0.90,
            0.80,
            0.85,
            0.75,
            0.88,
            0.82,
        ),
        candidate_values=(
            0.86,
            0.76,
            0.82,
            0.72,
            0.84,
            0.78,
        ),
        minimum_retention_ratio=0.90,
        evidence_record_ids=(
            "evidence-future-freedom-001",
        ),
    )

    matched_experiment = CoalitionMatchedExperiment(
        experiment_id="experiment-001",
        branches=_branches(),
        acceptance_contract_id="acceptance-001",
        independent_evaluator=True,
        frozen_acceptance_criteria=True,
        matched_compute_budget=True,
        multiple_seeds=True,
    )

    equivalence_class = (
        MicroscopicTicketEquivalenceClass(
            class_id="ticket-class-001",
            coalition_ids=(
                "coalition-main",
                "coalition-alternative",
            ),
            terminal_organizational_class_id="terminal-class-001",
            invariant_function_record_ids=(
                "invariant-001",
            ),
            admissible_corridor_ids=(
                "corridor-001",
                "corridor-002",
            ),
            evidence_record_ids=(
                "evidence-equivalence-001",
            ),
        )
    )

    nomination = ProspectiveCoalitionNomination(
        nomination_id="nomination-001",
        coalition_id="coalition-main",
        retrospective_atlas_id="atlas-001",
        matched_experiment_id="experiment-001",
        acceptance_contract_id="acceptance-001",
        evidence_record_ids=(
            "evidence-nomination-001",
        ),
    )

    authority_contract = (
        ReversiblePruningAuthorityContract(
            contract_id="pruning-contract-001",
            coalition_id="coalition-main",
            rollback_checkpoint_id="rollback-001",
            release_criteria_ids=(
                "release-criteria-001",
            ),
            maximum_sparsity=0.80,
            maximum_step_removal=0.10,
            recovery_budget_steps=32,
        )
    )

    return RCAFCarrierCoalitionBundle(
        bundle_id="bundle-001",
        coalition=coalition,
        validity_relations=validity_relations,
        role_evidence=role_evidence,
        scaffold_dependence=scaffold_dependence,
        scaffold_release=scaffold_release,
        turbulence_channels=(
            turbulence,
        ),
        future_freedom=future_freedom,
        matched_experiment=matched_experiment,
        equivalence_class=equivalence_class,
        nomination=nomination,
        authority_contract=authority_contract,
    )


def test_schema_is_explicit():
    assert RCAF_CARRIER_COALITION_SCHEMA == (
        "RCAF-CARRIER-COALITION-0.1"
    )


def test_component_roles_are_named_and_nonexclusive():
    evidence = _bundle().role_evidence[0]

    assert tuple(
        evidence.role_vector
    ) == COMPONENT_ROLE_NAMES

    assert evidence.role_vector[
        "payload"
    ] == 0.95

    assert evidence.role_vector[
        "routing"
    ] == 0.40

    assert evidence.role_assignment_nonexclusive is True


def test_turbulence_preserves_all_components():
    channel = _bundle().turbulence_channels[0]

    assert tuple(
        channel.component_vector
    ) == TURBULENCE_COMPONENT_NAMES

    assert len(
        channel.component_vector
    ) == 10

    assert (
        channel.collapsed_score_authoritative
        is False
    )


def test_validity_requires_all_relational_conditions():
    relation = _bundle().validity_relations[0]

    assert relation.valid is True

    altered = replace(
        relation,
        phase_compatible=False,
    )

    assert altered.valid is False


def test_release_admissibility_requires_every_dimension():
    release = _bundle().scaffold_release

    assert release.release_admissible is True

    altered = replace(
        release,
        transfer_preserved=False,
    )

    assert altered.release_admissible is False


def test_future_freedom_is_componentwise():
    record = _bundle().future_freedom

    assert record.preserved is True

    assert set(
        record.retention_by_component
    ) == {
        "task_adaptability",
        "routing_reserve",
        "recovery_capacity",
        "transfer_capacity",
        "perturbation_tolerance",
        "alternative_path_capacity",
    }


def test_coalition_can_be_nomination_ready_without_authority():
    coalition = _bundle().coalition

    assert coalition.nomination_ready is True
    assert coalition.authority_eligible is False
    assert coalition.observer_only is True


def test_matched_experiment_requires_a0_through_a8():
    experiment = _bundle().matched_experiment

    assert {
        branch.arm
        for branch in experiment.branches
    } == set(
        COUNTERFACTUAL_ARMS
    )

    with pytest.raises(
        CarrierCoalitionContractError,
        match="A0 through A8",
    ):
        replace(
            experiment,
            branches=experiment.branches[:-1],
        )


def test_ticket_equivalence_does_not_require_same_microstate():
    ticket_class = _bundle().equivalence_class

    assert (
        ticket_class.equivalent_terminal_organization
        is True
    )

    assert (
        ticket_class.identical_microstate_required
        is False
    )


def test_bundle_is_deterministic_and_private():
    first = _bundle().canonical_json()
    second = _bundle().canonical_json()

    assert first == second

    data = json.loads(
        first
    )

    assert data["schema"] == (
        RCAF_CARRIER_COALITION_SCHEMA
    )

    assert data["raw_content_stored"] is False

    assert (
        data["content_fingerprint_stored"]
        is False
    )

    assert (
        data["authority_contract"][
            "authority_status"
        ]
        == "observe_only"
    )


def test_bundle_rejects_nonmember_relation():
    bundle = _bundle()

    invalid_relation = replace(
        bundle.validity_relations[0],
        source_member_id="member-outside",
    )

    with pytest.raises(
        CarrierCoalitionContractError,
        match="non-member",
    ):
        replace(
            bundle,
            validity_relations=(
                invalid_relation,
            )
            + bundle.validity_relations[1:],
        )


def test_authority_contract_rejects_nonobserver_status():
    contract = _bundle().authority_contract

    with pytest.raises(
        CarrierCoalitionContractError,
        match="observe_only",
    ):
        replace(
            contract,
            authority_status="bounded",
        )


def test_nomination_cannot_release_authority():
    nomination = _bundle().nomination

    with pytest.raises(
        CarrierCoalitionContractError,
        match="safeguards failed",
    ):
        replace(
            nomination,
            authority_withheld=False,
        )


def test_raw_content_storage_is_rejected():
    bundle = _bundle()

    with pytest.raises(
        CarrierCoalitionContractError,
        match="raw content",
    ):
        replace(
            bundle,
            raw_content_stored=True,
        )


def test_duplicate_coalition_members_are_rejected():
    coalition = _bundle().coalition

    with pytest.raises(
        CarrierCoalitionContractError,
        match="duplicates",
    ):
        replace(
            coalition,
            member_ids=(
                "member-payload",
                "member-payload",
            ),
        )


def test_role_vector_must_preserve_every_component():
    evidence = _bundle().role_evidence[0]

    with pytest.raises(
        CarrierCoalitionContractError,
        match="8 components",
    ):
        replace(
            evidence,
            role_values=(
                0.5,
                0.5,
            ),
        )
