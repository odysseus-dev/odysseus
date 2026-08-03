# ============================================================
# src/rcaf/future_authority_ledger.py
# ============================================================

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from src.rcaf.organizational_ledger import (
    MetricVector,
)


RCAF_FUTURE_AUTHORITY_SCHEMA = (
    "RCAF-FUTURE-CONDITIONED-AUTHORITY-0.1"
)

MATCHED_CAUSAL_BRANCH_ROLES = frozenset(
    {
        "A0_base_preserve",
        "A1_valid_candidate",
        "A2_sham",
        "A3_wrong_context",
        "A4_over_boundary",
        "A5_support_withdrawal",
    }
)

BOUNDARY_RELATIONS = frozenset(
    {
        "within_boundary",
        "sham",
        "wrong_context",
        "over_boundary",
        "support_withdrawal",
    }
)

VERIFICATION_STATES = frozenset(
    {
        "unverified",
        "partial",
        "verified",
        "contradictory",
        "insufficient",
        "rejected",
    }
)

GATE_EVIDENCE_MATURITY_STATES = frozenset(
    {
        "unobserved",
        "candidate",
        "retrospectively_identified",
        "replicated",
        "causally_supported",
        "calibrated",
        "prospectively_validated",
    }
)

GATE_AUTHORITY_STATES = frozenset(
    {
        "observe_only",
        "nominated",
        "sandbox_candidate",
        "sandboxed",
        "bounded_authorized",
        "released",
        "revoked",
        "rejected",
    }
)

CALIBRATION_STATES = frozenset(
    {
        "uncalibrated",
        "empirical_frequency",
        "calibrated",
    }
)

FUTURE_BASIN_PULL_STATES = frozenset(
    {
        "unresolved",
        "weak",
        "observed",
        "elevated",
    }
)

NOMINATION_RECOMMENDATIONS = frozenset(
    {
        "observe",
        "sandbox_candidate",
        "defer",
        "reject",
    }
)

_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$"
)


class FutureAuthorityLedgerError(ValueError):
    """Raised when a future-conditioned authority contract is invalid."""


def _require_identifier(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise FutureAuthorityLedgerError(
            f"{field_name} must be a static structural identifier"
        )

    return normalized


def _optional_identifier(
    field_name: str,
    value: str | None,
) -> str | None:
    if value is None:
        return None

    return _require_identifier(
        field_name,
        value,
    )


def _require_choice(
    field_name: str,
    value: str,
    allowed: frozenset[str],
) -> str:
    normalized = str(value).strip()

    if normalized not in allowed:
        raise FutureAuthorityLedgerError(
            f"{field_name} must be one of {sorted(allowed)!r}; "
            f"received {normalized!r}"
        )

    return normalized


def _require_boolean(
    field_name: str,
    value: bool,
) -> bool:
    if not isinstance(value, bool):
        raise FutureAuthorityLedgerError(
            f"{field_name} must be bool"
        )

    return value


def _require_nonnegative_integer(
    field_name: str,
    value: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise FutureAuthorityLedgerError(
            f"{field_name} must be a non-negative integer"
        )

    return value


def _optional_probability(
    field_name: str,
    value: float | None,
) -> float | None:
    if value is None:
        return None

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise FutureAuthorityLedgerError(
            f"{field_name} must be a probability or None"
        )

    normalized = float(value)

    if not 0.0 <= normalized <= 1.0:
        raise FutureAuthorityLedgerError(
            f"{field_name} must be between 0 and 1"
        )

    return normalized


def _require_vector(
    field_name: str,
    value: MetricVector,
    *,
    nonempty: bool = False,
) -> MetricVector:
    if not isinstance(value, MetricVector):
        raise FutureAuthorityLedgerError(
            f"{field_name} must be MetricVector"
        )

    if nonempty and not value.components:
        raise FutureAuthorityLedgerError(
            f"{field_name} must not be empty"
        )

    return value


def _normalize_identifiers(
    field_name: str,
    values: tuple[str, ...],
    *,
    require_nonempty: bool = False,
) -> tuple[str, ...]:
    normalized = tuple(
        _require_identifier(
            field_name,
            value,
        )
        for value in values
    )

    if require_nonempty and not normalized:
        raise FutureAuthorityLedgerError(
            f"{field_name} must not be empty"
        )

    if len(set(normalized)) != len(normalized):
        raise FutureAuthorityLedgerError(
            f"{field_name} contains duplicate identifiers"
        )

    return normalized


@dataclass(frozen=True)
class TerminalOrganizationalContract:
    contract_id: str
    terminal_class_id: str
    benefit_requirements: MetricVector
    persistence_requirements: MetricVector
    harm_bounds: MetricVector
    containment_requirements: MetricVector
    absorption_requirements: MetricVector
    future_freedom_minima: MetricVector
    continuity_constraint_ids: tuple[str, ...]
    release_condition_ids: tuple[str, ...]
    rollback_mechanism_ids: tuple[str, ...]
    allowed_variation_ids: tuple[str, ...]
    forbidden_condition_ids: tuple[str, ...]
    authority_non_expansion_required: bool = True
    independent_verification_required: bool = True
    frozen_before_treatment: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "contract_id",
            "terminal_class_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "benefit_requirements",
            "persistence_requirements",
            "harm_bounds",
            "containment_requirements",
            "absorption_requirements",
            "future_freedom_minima",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                    nonempty=True,
                ),
            )

        for field_name in (
            "continuity_constraint_ids",
            "release_condition_ids",
            "rollback_mechanism_ids",
            "allowed_variation_ids",
            "forbidden_condition_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=True,
                ),
            )

        for field_name in (
            "authority_non_expansion_required",
            "independent_verification_required",
            "frozen_before_treatment",
        ):
            value = _require_boolean(
                field_name,
                getattr(self, field_name),
            )

            if value is not True:
                raise FutureAuthorityLedgerError(
                    f"{field_name} must remain True"
                )

            object.__setattr__(
                self,
                field_name,
                value,
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "contract_id": self.contract_id,
            "terminal_class_id": self.terminal_class_id,
            "benefit_requirements": (
                self.benefit_requirements.to_dict()
            ),
            "persistence_requirements": (
                self.persistence_requirements.to_dict()
            ),
            "harm_bounds": self.harm_bounds.to_dict(),
            "containment_requirements": (
                self.containment_requirements.to_dict()
            ),
            "absorption_requirements": (
                self.absorption_requirements.to_dict()
            ),
            "future_freedom_minima": (
                self.future_freedom_minima.to_dict()
            ),
            "continuity_constraint_ids": list(
                self.continuity_constraint_ids
            ),
            "release_condition_ids": list(
                self.release_condition_ids
            ),
            "rollback_mechanism_ids": list(
                self.rollback_mechanism_ids
            ),
            "allowed_variation_ids": list(
                self.allowed_variation_ids
            ),
            "forbidden_condition_ids": list(
                self.forbidden_condition_ids
            ),
            "authority_non_expansion_required": True,
            "independent_verification_required": True,
            "frozen_before_treatment": True,
        }


@dataclass(frozen=True)
class ForwardTrajectoryBranch:
    branch_id: str
    seed_id: str
    carrier_state_id: str
    hidden_context_class_id: str
    disturbance_class_id: str
    terminal_class_id: str
    corridor_id: str
    safe_corridor_occupied: bool
    boundary_compliant: bool
    release_available: bool
    future_freedom: MetricVector
    outcome_metrics: MetricVector
    failure_mode_ids: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "branch_id",
            "seed_id",
            "carrier_state_id",
            "hidden_context_class_id",
            "disturbance_class_id",
            "terminal_class_id",
            "corridor_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "safe_corridor_occupied",
            "boundary_compliant",
            "release_available",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_boolean(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "future_freedom",
            "outcome_metrics",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                    nonempty=True,
                ),
            )

        object.__setattr__(
            self,
            "failure_mode_ids",
            _normalize_identifiers(
                "failure_mode_ids",
                self.failure_mode_ids,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "branch_id": self.branch_id,
            "seed_id": self.seed_id,
            "carrier_state_id": self.carrier_state_id,
            "hidden_context_class_id": (
                self.hidden_context_class_id
            ),
            "disturbance_class_id": (
                self.disturbance_class_id
            ),
            "terminal_class_id": self.terminal_class_id,
            "corridor_id": self.corridor_id,
            "safe_corridor_occupied": (
                self.safe_corridor_occupied
            ),
            "boundary_compliant": self.boundary_compliant,
            "release_available": self.release_available,
            "future_freedom": self.future_freedom.to_dict(),
            "outcome_metrics": self.outcome_metrics.to_dict(),
            "failure_mode_ids": list(self.failure_mode_ids),
        }


@dataclass(frozen=True)
class ForwardReachabilityEvidence:
    forward_map_id: str
    reachable_set_id: str
    current_state_id: str
    candidate_action_id: str
    branches: tuple[ForwardTrajectoryBranch, ...]
    reachable_terminal_class_ids: tuple[str, ...]
    safe_corridor_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    forward_reachable: bool

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "forward_map_id",
            "reachable_set_id",
            "current_state_id",
            "candidate_action_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        branches = tuple(self.branches)

        if not branches:
            raise FutureAuthorityLedgerError(
                "branches must not be empty"
            )

        branch_ids = [
            branch.branch_id
            for branch in branches
        ]

        if len(set(branch_ids)) != len(branch_ids):
            raise FutureAuthorityLedgerError(
                "branches contain duplicate branch IDs"
            )

        object.__setattr__(
            self,
            "branches",
            branches,
        )

        for field_name in (
            "reachable_terminal_class_ids",
            "safe_corridor_ids",
            "evidence_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=True,
                ),
            )

        object.__setattr__(
            self,
            "forward_reachable",
            _require_boolean(
                "forward_reachable",
                self.forward_reachable,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "forward_map_id": self.forward_map_id,
            "reachable_set_id": self.reachable_set_id,
            "current_state_id": self.current_state_id,
            "candidate_action_id": self.candidate_action_id,
            "branches": [
                branch.to_dict()
                for branch in self.branches
            ],
            "reachable_terminal_class_ids": list(
                self.reachable_terminal_class_ids
            ),
            "safe_corridor_ids": list(
                self.safe_corridor_ids
            ),
            "evidence_ids": list(self.evidence_ids),
            "forward_reachable": self.forward_reachable,
        }


@dataclass(frozen=True)
class BackwardPredecessorEvidence:
    backward_map_id: str
    predecessor_set_id: str
    future_contract_id: str
    required_reference_contract_id: str
    required_condition_ids: tuple[str, ...]
    satisfied_condition_ids: tuple[str, ...]
    unmet_condition_ids: tuple[str, ...]
    forbidden_predecessor_condition_ids: tuple[str, ...]
    required_carrier_relation_ids: tuple[str, ...]
    required_boundary_ids: tuple[str, ...]
    required_evaluator_ids: tuple[str, ...]
    required_release_path_ids: tuple[str, ...]
    required_rollback_mechanism_ids: tuple[str, ...]
    uncertainty: MetricVector
    backward_consistent: bool

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "backward_map_id",
            "predecessor_set_id",
            "future_contract_id",
            "required_reference_contract_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "required_condition_ids",
            "satisfied_condition_ids",
            "unmet_condition_ids",
            "forbidden_predecessor_condition_ids",
            "required_carrier_relation_ids",
            "required_boundary_ids",
            "required_evaluator_ids",
            "required_release_path_ids",
            "required_rollback_mechanism_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=(
                        field_name
                        in {
                            "required_condition_ids",
                            "required_carrier_relation_ids",
                            "required_boundary_ids",
                            "required_evaluator_ids",
                            "required_release_path_ids",
                            "required_rollback_mechanism_ids",
                        }
                    ),
                ),
            )

        required = set(self.required_condition_ids)
        satisfied = set(self.satisfied_condition_ids)
        unmet = set(self.unmet_condition_ids)

        if not satisfied.issubset(required):
            raise FutureAuthorityLedgerError(
                "satisfied conditions must occur in required conditions"
            )

        if not unmet.issubset(required):
            raise FutureAuthorityLedgerError(
                "unmet conditions must occur in required conditions"
            )

        if satisfied & unmet:
            raise FutureAuthorityLedgerError(
                "conditions cannot be both satisfied and unmet"
            )

        object.__setattr__(
            self,
            "uncertainty",
            _require_vector(
                "uncertainty",
                self.uncertainty,
                nonempty=True,
            ),
        )

        object.__setattr__(
            self,
            "backward_consistent",
            _require_boolean(
                "backward_consistent",
                self.backward_consistent,
            ),
        )

        if self.backward_consistent:
            if unmet:
                raise FutureAuthorityLedgerError(
                    "backward-consistent evidence cannot contain "
                    "unmet conditions"
                )

            if satisfied != required:
                raise FutureAuthorityLedgerError(
                    "backward-consistent evidence must satisfy "
                    "every required condition"
                )

    def to_dict(
        self,
    ) -> dict:
        return {
            "backward_map_id": self.backward_map_id,
            "predecessor_set_id": self.predecessor_set_id,
            "future_contract_id": self.future_contract_id,
            "required_reference_contract_id": (
                self.required_reference_contract_id
            ),
            "required_condition_ids": list(
                self.required_condition_ids
            ),
            "satisfied_condition_ids": list(
                self.satisfied_condition_ids
            ),
            "unmet_condition_ids": list(
                self.unmet_condition_ids
            ),
            "forbidden_predecessor_condition_ids": list(
                self.forbidden_predecessor_condition_ids
            ),
            "required_carrier_relation_ids": list(
                self.required_carrier_relation_ids
            ),
            "required_boundary_ids": list(
                self.required_boundary_ids
            ),
            "required_evaluator_ids": list(
                self.required_evaluator_ids
            ),
            "required_release_path_ids": list(
                self.required_release_path_ids
            ),
            "required_rollback_mechanism_ids": list(
                self.required_rollback_mechanism_ids
            ),
            "uncertainty": self.uncertainty.to_dict(),
            "backward_consistent": self.backward_consistent,
        }


@dataclass(frozen=True)
class BidirectionalConsistencyRecord:
    consistency_record_id: str
    forward_reachable_set_id: str
    backward_predecessor_set_id: str
    shared_corridor_ids: tuple[str, ...]
    excluded_trajectory_ids: tuple[str, ...]
    consistency_evidence_ids: tuple[str, ...]
    consistency_failure_ids: tuple[str, ...]
    terminal_class_agreement: bool
    intermediate_boundary_compliance: bool
    future_freedom_compliance: bool
    release_compliance: bool
    gate_nominated: bool
    gate_admissible: bool

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "consistency_record_id",
            "forward_reachable_set_id",
            "backward_predecessor_set_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "shared_corridor_ids",
            "excluded_trajectory_ids",
            "consistency_evidence_ids",
            "consistency_failure_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=(
                        field_name
                        in {
                            "shared_corridor_ids",
                            "consistency_evidence_ids",
                        }
                    ),
                ),
            )

        for field_name in (
            "terminal_class_agreement",
            "intermediate_boundary_compliance",
            "future_freedom_compliance",
            "release_compliance",
            "gate_nominated",
            "gate_admissible",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_boolean(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if self.gate_admissible:
            required = (
                self.terminal_class_agreement
                and self.intermediate_boundary_compliance
                and self.future_freedom_compliance
                and self.release_compliance
                and self.gate_nominated
                and not self.consistency_failure_ids
            )

            if not required:
                raise FutureAuthorityLedgerError(
                    "gate_admissible requires complete bidirectional "
                    "consistency and no consistency failures"
                )

    def to_dict(
        self,
    ) -> dict:
        return {
            "consistency_record_id": self.consistency_record_id,
            "forward_reachable_set_id": (
                self.forward_reachable_set_id
            ),
            "backward_predecessor_set_id": (
                self.backward_predecessor_set_id
            ),
            "shared_corridor_ids": list(
                self.shared_corridor_ids
            ),
            "excluded_trajectory_ids": list(
                self.excluded_trajectory_ids
            ),
            "consistency_evidence_ids": list(
                self.consistency_evidence_ids
            ),
            "consistency_failure_ids": list(
                self.consistency_failure_ids
            ),
            "terminal_class_agreement": (
                self.terminal_class_agreement
            ),
            "intermediate_boundary_compliance": (
                self.intermediate_boundary_compliance
            ),
            "future_freedom_compliance": (
                self.future_freedom_compliance
            ),
            "release_compliance": self.release_compliance,
            "gate_nominated": self.gate_nominated,
            "gate_admissible": self.gate_admissible,
        }


@dataclass(frozen=True)
class MatchedCausalBranch:
    branch_id: str
    branch_role: str
    shared_initial_state_contract_id: str
    intervention_id: str
    context_id: str
    boundary_relation: str
    terminal_class_id: str
    evaluator_id: str
    support_present: bool
    execution_performed: bool
    verification_state: str
    benefit: MetricVector
    harm: MetricVector
    containment: MetricVector
    future_freedom: MetricVector
    absorption: MetricVector
    persistence: MetricVector
    release: MetricVector

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "branch_id",
            "shared_initial_state_contract_id",
            "intervention_id",
            "context_id",
            "terminal_class_id",
            "evaluator_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "branch_role",
            _require_choice(
                "branch_role",
                self.branch_role,
                MATCHED_CAUSAL_BRANCH_ROLES,
            ),
        )

        object.__setattr__(
            self,
            "boundary_relation",
            _require_choice(
                "boundary_relation",
                self.boundary_relation,
                BOUNDARY_RELATIONS,
            ),
        )

        for field_name in (
            "support_present",
            "execution_performed",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_boolean(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "verification_state",
            _require_choice(
                "verification_state",
                self.verification_state,
                VERIFICATION_STATES,
            ),
        )

        for field_name in (
            "benefit",
            "harm",
            "containment",
            "future_freedom",
            "absorption",
            "persistence",
            "release",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                    nonempty=True,
                ),
            )

        if (
            self.branch_role
            == "A5_support_withdrawal"
            and self.support_present
        ):
            raise FutureAuthorityLedgerError(
                "support-withdrawal branch cannot retain support"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "branch_id": self.branch_id,
            "branch_role": self.branch_role,
            "shared_initial_state_contract_id": (
                self.shared_initial_state_contract_id
            ),
            "intervention_id": self.intervention_id,
            "context_id": self.context_id,
            "boundary_relation": self.boundary_relation,
            "terminal_class_id": self.terminal_class_id,
            "evaluator_id": self.evaluator_id,
            "support_present": self.support_present,
            "execution_performed": self.execution_performed,
            "verification_state": self.verification_state,
            "benefit": self.benefit.to_dict(),
            "harm": self.harm.to_dict(),
            "containment": self.containment.to_dict(),
            "future_freedom": self.future_freedom.to_dict(),
            "absorption": self.absorption.to_dict(),
            "persistence": self.persistence.to_dict(),
            "release": self.release.to_dict(),
        }


@dataclass(frozen=True)
class MatchedCausalExperiment:
    experiment_id: str
    frozen_future_contract_id: str
    frozen_acceptance_criteria_id: str
    independent_evaluator_id: str
    branches: tuple[MatchedCausalBranch, ...]
    treatment_effects: MetricVector
    control_separation: MetricVector
    wrong_context_separation: MetricVector
    over_boundary_failure: MetricVector
    support_withdrawal_persistence: MetricVector
    cross_seed_robustness: MetricVector
    cross_carrier_robustness: MetricVector
    evaluator_agreement: MetricVector
    valid_treatment_preferential: bool
    controls_weaker: bool
    withdrawal_persistent: bool
    independent_verification_passed: bool
    causal_support_established: bool

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "experiment_id",
            "frozen_future_contract_id",
            "frozen_acceptance_criteria_id",
            "independent_evaluator_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        branches = tuple(self.branches)

        roles = [
            branch.branch_role
            for branch in branches
        ]

        if set(roles) != MATCHED_CAUSAL_BRANCH_ROLES:
            raise FutureAuthorityLedgerError(
                "matched experiment must contain exactly A0 through A5"
            )

        if len(roles) != len(MATCHED_CAUSAL_BRANCH_ROLES):
            raise FutureAuthorityLedgerError(
                "matched experiment contains duplicate branch roles"
            )

        initial_states = {
            branch.shared_initial_state_contract_id
            for branch in branches
        }

        if len(initial_states) != 1:
            raise FutureAuthorityLedgerError(
                "matched branches must share one initial-state contract"
            )

        object.__setattr__(
            self,
            "branches",
            branches,
        )

        for field_name in (
            "treatment_effects",
            "control_separation",
            "wrong_context_separation",
            "over_boundary_failure",
            "support_withdrawal_persistence",
            "cross_seed_robustness",
            "cross_carrier_robustness",
            "evaluator_agreement",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                    nonempty=True,
                ),
            )

        for field_name in (
            "valid_treatment_preferential",
            "controls_weaker",
            "withdrawal_persistent",
            "independent_verification_passed",
            "causal_support_established",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_boolean(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if self.causal_support_established:
            if not (
                self.valid_treatment_preferential
                and self.controls_weaker
                and self.withdrawal_persistent
                and self.independent_verification_passed
            ):
                raise FutureAuthorityLedgerError(
                    "causal support requires treatment preference, "
                    "control separation, withdrawal persistence and "
                    "independent verification"
                )

    def to_dict(
        self,
    ) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "frozen_future_contract_id": (
                self.frozen_future_contract_id
            ),
            "frozen_acceptance_criteria_id": (
                self.frozen_acceptance_criteria_id
            ),
            "independent_evaluator_id": (
                self.independent_evaluator_id
            ),
            "branches": [
                branch.to_dict()
                for branch in self.branches
            ],
            "treatment_effects": (
                self.treatment_effects.to_dict()
            ),
            "control_separation": (
                self.control_separation.to_dict()
            ),
            "wrong_context_separation": (
                self.wrong_context_separation.to_dict()
            ),
            "over_boundary_failure": (
                self.over_boundary_failure.to_dict()
            ),
            "support_withdrawal_persistence": (
                self.support_withdrawal_persistence.to_dict()
            ),
            "cross_seed_robustness": (
                self.cross_seed_robustness.to_dict()
            ),
            "cross_carrier_robustness": (
                self.cross_carrier_robustness.to_dict()
            ),
            "evaluator_agreement": (
                self.evaluator_agreement.to_dict()
            ),
            "valid_treatment_preferential": (
                self.valid_treatment_preferential
            ),
            "controls_weaker": self.controls_weaker,
            "withdrawal_persistent": (
                self.withdrawal_persistent
            ),
            "independent_verification_passed": (
                self.independent_verification_passed
            ),
            "causal_support_established": (
                self.causal_support_established
            ),
        }


@dataclass(frozen=True)
class AntiCoercionAuthorityCheck:
    check_id: str
    prediction_preceded_intervention: bool
    terminal_contract_frozen: bool
    acceptance_criteria_frozen: bool
    evaluator_independent: bool
    target_rewrite_prevented: bool
    evidence_rewrite_prevented: bool
    control_branches_preserved: bool
    valid_treatment_preferential: bool
    support_withdrawal_persistent: bool
    passed: bool

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "check_id",
            _require_identifier(
                "check_id",
                self.check_id,
            ),
        )

        condition_names = (
            "prediction_preceded_intervention",
            "terminal_contract_frozen",
            "acceptance_criteria_frozen",
            "evaluator_independent",
            "target_rewrite_prevented",
            "evidence_rewrite_prevented",
            "control_branches_preserved",
            "valid_treatment_preferential",
            "support_withdrawal_persistent",
        )

        conditions = []

        for field_name in condition_names:
            value = _require_boolean(
                field_name,
                getattr(self, field_name),
            )

            object.__setattr__(
                self,
                field_name,
                value,
            )

            conditions.append(value)

        object.__setattr__(
            self,
            "passed",
            _require_boolean(
                "passed",
                self.passed,
            ),
        )

        if self.passed != all(conditions):
            raise FutureAuthorityLedgerError(
                "anti-coercion result must equal all safeguards"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class RetrospectivePrecursorAtlasRecord:
    atlas_record_id: str
    completed_trajectory_id: str
    terminal_class_id: str
    earliest_reliable_precursor_id: str
    precursor_class_id: str
    decisive_carrier_coalition_ids: tuple[str, ...]
    boundary_crossing_event_id: str
    alternative_future_collapse_event_id: str
    absorption_likelihood_event_id: str
    release_danger_event_id: str
    inference_method_id: str
    validation_population_id: str
    replication_evidence_ids: tuple[str, ...]
    replicated: bool
    authority_granted: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "atlas_record_id",
            "completed_trajectory_id",
            "terminal_class_id",
            "earliest_reliable_precursor_id",
            "precursor_class_id",
            "boundary_crossing_event_id",
            "alternative_future_collapse_event_id",
            "absorption_likelihood_event_id",
            "release_danger_event_id",
            "inference_method_id",
            "validation_population_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "decisive_carrier_coalition_ids",
            "replication_evidence_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=True,
                ),
            )

        object.__setattr__(
            self,
            "replicated",
            _require_boolean(
                "replicated",
                self.replicated,
            ),
        )

        authority_granted = _require_boolean(
            "authority_granted",
            self.authority_granted,
        )

        if authority_granted:
            raise FutureAuthorityLedgerError(
                "retrospective atlas extraction cannot grant authority"
            )

        object.__setattr__(
            self,
            "authority_granted",
            False,
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "atlas_record_id": self.atlas_record_id,
            "completed_trajectory_id": self.completed_trajectory_id,
            "terminal_class_id": self.terminal_class_id,
            "earliest_reliable_precursor_id": (
                self.earliest_reliable_precursor_id
            ),
            "precursor_class_id": self.precursor_class_id,
            "decisive_carrier_coalition_ids": list(
                self.decisive_carrier_coalition_ids
            ),
            "boundary_crossing_event_id": (
                self.boundary_crossing_event_id
            ),
            "alternative_future_collapse_event_id": (
                self.alternative_future_collapse_event_id
            ),
            "absorption_likelihood_event_id": (
                self.absorption_likelihood_event_id
            ),
            "release_danger_event_id": (
                self.release_danger_event_id
            ),
            "inference_method_id": self.inference_method_id,
            "validation_population_id": (
                self.validation_population_id
            ),
            "replication_evidence_ids": list(
                self.replication_evidence_ids
            ),
            "replicated": self.replicated,
            "authority_granted": False,
        }


@dataclass(frozen=True)
class ProspectiveGateNomination:
    nomination_id: str
    observed_precursor_class_id: str
    matched_atlas_invariant_id: str
    reference_contract_id: str
    corridor_match_id: str
    deviation_metrics: MetricVector
    evidence_ids: tuple[str, ...]
    recommendation: str
    authority_withheld: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "nomination_id",
            "observed_precursor_class_id",
            "matched_atlas_invariant_id",
            "reference_contract_id",
            "corridor_match_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "deviation_metrics",
            _require_vector(
                "deviation_metrics",
                self.deviation_metrics,
                nonempty=True,
            ),
        )

        object.__setattr__(
            self,
            "evidence_ids",
            _normalize_identifiers(
                "evidence_ids",
                self.evidence_ids,
                require_nonempty=True,
            ),
        )

        object.__setattr__(
            self,
            "recommendation",
            _require_choice(
                "recommendation",
                self.recommendation,
                NOMINATION_RECOMMENDATIONS,
            ),
        )

        authority_withheld = _require_boolean(
            "authority_withheld",
            self.authority_withheld,
        )

        if authority_withheld is not True:
            raise FutureAuthorityLedgerError(
                "prospective nomination must withhold authority"
            )

        object.__setattr__(
            self,
            "authority_withheld",
            True,
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "nomination_id": self.nomination_id,
            "observed_precursor_class_id": (
                self.observed_precursor_class_id
            ),
            "matched_atlas_invariant_id": (
                self.matched_atlas_invariant_id
            ),
            "reference_contract_id": self.reference_contract_id,
            "corridor_match_id": self.corridor_match_id,
            "deviation_metrics": (
                self.deviation_metrics.to_dict()
            ),
            "evidence_ids": list(self.evidence_ids),
            "recommendation": self.recommendation,
            "authority_withheld": True,
        }


@dataclass(frozen=True)
class FutureGateCalibration:
    calibration_id: str
    status: str
    future_class_confidence: float | None
    future_basin_pull: str
    sample_count: int
    effective_sample_count: int
    calibration_population_id: str
    calibration_error: float | None
    out_of_distribution: bool
    scenario_tail_metrics: MetricVector
    confidence_interval: MetricVector = field(
        default_factory=MetricVector
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "calibration_id",
            _require_identifier(
                "calibration_id",
                self.calibration_id,
            ),
        )

        object.__setattr__(
            self,
            "status",
            _require_choice(
                "status",
                self.status,
                CALIBRATION_STATES,
            ),
        )

        object.__setattr__(
            self,
            "future_class_confidence",
            _optional_probability(
                "future_class_confidence",
                self.future_class_confidence,
            ),
        )

        object.__setattr__(
            self,
            "future_basin_pull",
            _require_choice(
                "future_basin_pull",
                self.future_basin_pull,
                FUTURE_BASIN_PULL_STATES,
            ),
        )

        for field_name in (
            "sample_count",
            "effective_sample_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_nonnegative_integer(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if self.effective_sample_count > self.sample_count:
            raise FutureAuthorityLedgerError(
                "effective sample count cannot exceed sample count"
            )

        object.__setattr__(
            self,
            "calibration_population_id",
            _require_identifier(
                "calibration_population_id",
                self.calibration_population_id,
            ),
        )

        object.__setattr__(
            self,
            "calibration_error",
            _optional_probability(
                "calibration_error",
                self.calibration_error,
            ),
        )

        object.__setattr__(
            self,
            "out_of_distribution",
            _require_boolean(
                "out_of_distribution",
                self.out_of_distribution,
            ),
        )

        object.__setattr__(
            self,
            "scenario_tail_metrics",
            _require_vector(
                "scenario_tail_metrics",
                self.scenario_tail_metrics,
                nonempty=True,
            ),
        )

        object.__setattr__(
            self,
            "confidence_interval",
            _require_vector(
                "confidence_interval",
                self.confidence_interval,
            ),
        )

        if self.status == "uncalibrated":
            if (
                self.future_class_confidence is not None
                or self.calibration_error is not None
                or self.confidence_interval.components
            ):
                raise FutureAuthorityLedgerError(
                    "uncalibrated evidence cannot claim confidence, "
                    "calibration error or confidence interval"
                )

        if self.status == "calibrated":
            if (
                self.future_class_confidence is None
                or self.calibration_error is None
                or not self.confidence_interval.components
            ):
                raise FutureAuthorityLedgerError(
                    "calibrated evidence requires confidence, "
                    "calibration error and confidence interval"
                )

    def to_dict(
        self,
    ) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "status": self.status,
            "future_class_confidence": (
                self.future_class_confidence
            ),
            "future_basin_pull": self.future_basin_pull,
            "sample_count": self.sample_count,
            "effective_sample_count": (
                self.effective_sample_count
            ),
            "calibration_population_id": (
                self.calibration_population_id
            ),
            "calibration_error": self.calibration_error,
            "out_of_distribution": self.out_of_distribution,
            "scenario_tail_metrics": (
                self.scenario_tail_metrics.to_dict()
            ),
            "confidence_interval": (
                self.confidence_interval.to_dict()
            ),
        }


@dataclass(frozen=True)
class FutureConditionedGateLifecycle:
    lifecycle_id: str
    evidence_maturity: str
    authority_status: str
    retrospective_atlas_record_id: str
    prospective_nomination_id: str
    causal_experiment_id: str
    anti_coercion_check_id: str
    calibration_id: str
    no_auto_promotion: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "lifecycle_id",
            "retrospective_atlas_record_id",
            "prospective_nomination_id",
            "causal_experiment_id",
            "anti_coercion_check_id",
            "calibration_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "evidence_maturity",
            _require_choice(
                "evidence_maturity",
                self.evidence_maturity,
                GATE_EVIDENCE_MATURITY_STATES,
            ),
        )

        object.__setattr__(
            self,
            "authority_status",
            _require_choice(
                "authority_status",
                self.authority_status,
                GATE_AUTHORITY_STATES,
            ),
        )

        no_auto_promotion = _require_boolean(
            "no_auto_promotion",
            self.no_auto_promotion,
        )

        if no_auto_promotion is not True:
            raise FutureAuthorityLedgerError(
                "future-gate evidence cannot auto-promote authority"
            )

        object.__setattr__(
            self,
            "no_auto_promotion",
            True,
        )

        if self.authority_status == "bounded_authorized":
            if self.evidence_maturity not in {
                "calibrated",
                "prospectively_validated",
            }:
                raise FutureAuthorityLedgerError(
                    "bounded authority requires calibrated or "
                    "prospectively validated evidence"
                )

    def to_dict(
        self,
    ) -> dict:
        return {
            "lifecycle_id": self.lifecycle_id,
            "evidence_maturity": self.evidence_maturity,
            "authority_status": self.authority_status,
            "retrospective_atlas_record_id": (
                self.retrospective_atlas_record_id
            ),
            "prospective_nomination_id": (
                self.prospective_nomination_id
            ),
            "causal_experiment_id": self.causal_experiment_id,
            "anti_coercion_check_id": (
                self.anti_coercion_check_id
            ),
            "calibration_id": self.calibration_id,
            "no_auto_promotion": True,
        }


@dataclass(frozen=True)
class BidirectionalMetaFieldReference:
    meta_field_record_id: str
    forward_transition_map_id: str
    backward_predecessor_map_id: str
    evidence_structure_id: str
    admissibility_authority_structure_id: str
    consistency_relation_id: str
    literal_retrocausality_claimed: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "meta_field_record_id",
            "forward_transition_map_id",
            "backward_predecessor_map_id",
            "evidence_structure_id",
            "admissibility_authority_structure_id",
            "consistency_relation_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        literal = _require_boolean(
            "literal_retrocausality_claimed",
            self.literal_retrocausality_claimed,
        )

        if literal:
            raise FutureAuthorityLedgerError(
                "RCAF does not claim literal backward causation"
            )

        object.__setattr__(
            self,
            "literal_retrocausality_claimed",
            False,
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "meta_field_record_id": self.meta_field_record_id,
            "forward_transition_map_id": (
                self.forward_transition_map_id
            ),
            "backward_predecessor_map_id": (
                self.backward_predecessor_map_id
            ),
            "evidence_structure_id": self.evidence_structure_id,
            "admissibility_authority_structure_id": (
                self.admissibility_authority_structure_id
            ),
            "consistency_relation_id": (
                self.consistency_relation_id
            ),
            "literal_retrocausality_claimed": False,
        }


@dataclass(frozen=True)
class FutureConditionedAuthorityEvidenceBundle:
    terminal_contract: TerminalOrganizationalContract
    forward_evidence: ForwardReachabilityEvidence
    backward_evidence: BackwardPredecessorEvidence
    consistency: BidirectionalConsistencyRecord
    causal_experiment: MatchedCausalExperiment
    anti_coercion: AntiCoercionAuthorityCheck
    retrospective_atlas: RetrospectivePrecursorAtlasRecord
    prospective_nomination: ProspectiveGateNomination
    calibration: FutureGateCalibration
    lifecycle: FutureConditionedGateLifecycle
    meta_field: BidirectionalMetaFieldReference
    raw_content_stored: bool = False
    content_fingerprint_stored: bool = False

    def __post_init__(
        self,
    ) -> None:
        if (
            self.backward_evidence.future_contract_id
            != self.terminal_contract.contract_id
        ):
            raise FutureAuthorityLedgerError(
                "backward evidence must use the terminal contract"
            )

        if (
            self.causal_experiment.frozen_future_contract_id
            != self.terminal_contract.contract_id
        ):
            raise FutureAuthorityLedgerError(
                "causal experiment must freeze the terminal contract"
            )

        if (
            self.terminal_contract.terminal_class_id
            not in self.forward_evidence.reachable_terminal_class_ids
        ):
            raise FutureAuthorityLedgerError(
                "forward evidence must include the terminal class"
            )

        if (
            self.consistency.forward_reachable_set_id
            != self.forward_evidence.reachable_set_id
        ):
            raise FutureAuthorityLedgerError(
                "consistency record forward-set linkage mismatch"
            )

        if (
            self.consistency.backward_predecessor_set_id
            != self.backward_evidence.predecessor_set_id
        ):
            raise FutureAuthorityLedgerError(
                "consistency record predecessor-set linkage mismatch"
            )

        if (
            self.meta_field.forward_transition_map_id
            != self.forward_evidence.forward_map_id
        ):
            raise FutureAuthorityLedgerError(
                "meta-field forward-map linkage mismatch"
            )

        if (
            self.meta_field.backward_predecessor_map_id
            != self.backward_evidence.backward_map_id
        ):
            raise FutureAuthorityLedgerError(
                "meta-field backward-map linkage mismatch"
            )

        if (
            self.meta_field.consistency_relation_id
            != self.consistency.consistency_record_id
        ):
            raise FutureAuthorityLedgerError(
                "meta-field consistency linkage mismatch"
            )

        if (
            self.lifecycle.retrospective_atlas_record_id
            != self.retrospective_atlas.atlas_record_id
        ):
            raise FutureAuthorityLedgerError(
                "lifecycle retrospective-atlas linkage mismatch"
            )

        if (
            self.lifecycle.prospective_nomination_id
            != self.prospective_nomination.nomination_id
        ):
            raise FutureAuthorityLedgerError(
                "lifecycle prospective-nomination linkage mismatch"
            )

        if (
            self.lifecycle.causal_experiment_id
            != self.causal_experiment.experiment_id
        ):
            raise FutureAuthorityLedgerError(
                "lifecycle causal-experiment linkage mismatch"
            )

        if (
            self.lifecycle.anti_coercion_check_id
            != self.anti_coercion.check_id
        ):
            raise FutureAuthorityLedgerError(
                "lifecycle anti-coercion linkage mismatch"
            )

        if (
            self.lifecycle.calibration_id
            != self.calibration.calibration_id
        ):
            raise FutureAuthorityLedgerError(
                "lifecycle calibration linkage mismatch"
            )

        if (
            self.prospective_nomination.observed_precursor_class_id
            != self.retrospective_atlas.precursor_class_id
        ):
            raise FutureAuthorityLedgerError(
                "prospective nomination must match the validated "
                "retrospective precursor class"
            )

        if not set(
            self.consistency.shared_corridor_ids
        ).issubset(
            set(
                self.forward_evidence.safe_corridor_ids
            )
        ):
            raise FutureAuthorityLedgerError(
                "shared consistency corridors must occur in "
                "the forward safe-corridor set"
            )

        if (
            self.prospective_nomination.corridor_match_id
            not in self.consistency.shared_corridor_ids
        ):
            raise FutureAuthorityLedgerError(
                "prospective nomination corridor mismatch"
            )

        if (
            self.retrospective_atlas.terminal_class_id
            != self.terminal_contract.terminal_class_id
        ):
            raise FutureAuthorityLedgerError(
                "retrospective terminal-class linkage mismatch"
            )

        if (
            self.calibration.calibration_population_id
            != self.retrospective_atlas.validation_population_id
        ):
            raise FutureAuthorityLedgerError(
                "calibration population linkage mismatch"
            )

        if (
            self.causal_experiment.independent_evaluator_id
            not in self.backward_evidence.required_evaluator_ids
        ):
            raise FutureAuthorityLedgerError(
                "independent evaluator is not a required predecessor"
            )

        branch_evaluators = {
            branch.evaluator_id
            for branch in self.causal_experiment.branches
        }

        if branch_evaluators != {
            self.causal_experiment.independent_evaluator_id
        }:
            raise FutureAuthorityLedgerError(
                "matched branches must use the frozen independent evaluator"
            )

        if (
            self.anti_coercion.valid_treatment_preferential
            != self.causal_experiment.valid_treatment_preferential
            or self.anti_coercion.support_withdrawal_persistent
            != self.causal_experiment.withdrawal_persistent
            or self.anti_coercion.evaluator_independent
            != self.causal_experiment.independent_verification_passed
        ):
            raise FutureAuthorityLedgerError(
                "anti-coercion evidence disagrees with the "
                "matched causal experiment"
            )

        if (
            self.causal_experiment.causal_support_established
            and not self.anti_coercion.passed
        ):
            raise FutureAuthorityLedgerError(
                "causal support cannot survive a failed anti-coercion check"
            )

        for field_name in (
            "raw_content_stored",
            "content_fingerprint_stored",
        ):
            value = _require_boolean(
                field_name,
                getattr(self, field_name),
            )

            if value is not False:
                raise FutureAuthorityLedgerError(
                    f"{field_name} must remain False"
                )

    @property
    def causal_authority_eligible(
        self,
    ) -> bool:
        return bool(
            self.consistency.gate_admissible
            and self.causal_experiment.causal_support_established
            and self.anti_coercion.passed
            and self.calibration.status == "calibrated"
            and self.lifecycle.evidence_maturity
            in {
                "calibrated",
                "prospectively_validated",
            }
            and self.lifecycle.authority_status
            in {
                "sandbox_candidate",
                "sandboxed",
                "bounded_authorized",
            }
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "schema": RCAF_FUTURE_AUTHORITY_SCHEMA,
            "terminal_contract": (
                self.terminal_contract.to_dict()
            ),
            "forward_evidence": self.forward_evidence.to_dict(),
            "backward_evidence": (
                self.backward_evidence.to_dict()
            ),
            "consistency": self.consistency.to_dict(),
            "causal_experiment": (
                self.causal_experiment.to_dict()
            ),
            "anti_coercion": self.anti_coercion.to_dict(),
            "retrospective_atlas": (
                self.retrospective_atlas.to_dict()
            ),
            "prospective_nomination": (
                self.prospective_nomination.to_dict()
            ),
            "calibration": self.calibration.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "meta_field": self.meta_field.to_dict(),
            "causal_authority_eligible": (
                self.causal_authority_eligible
            ),
            "raw_content_stored": False,
            "content_fingerprint_stored": False,
        }

    def canonical_json(
        self,
    ) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
