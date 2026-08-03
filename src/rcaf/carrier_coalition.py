# ============================================================
# src/rcaf/carrier_coalition.py
# ============================================================

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


RCAF_CARRIER_COALITION_SCHEMA = (
    "RCAF-CARRIER-COALITION-0.1"
)

CARRIER_RELATION_KINDS = frozenset(
    {
        "support",
        "route",
        "buffer",
        "reserve",
        "compete",
        "inhibit",
        "synchronize",
        "scaffold",
        "payload",
    }
)

COMPONENT_ROLE_NAMES = (
    "payload",
    "scaffold",
    "routing",
    "buffer",
    "reserve",
    "competitor",
    "redundancy",
    "turbulence",
)

TURBULENCE_COMPONENT_NAMES = (
    "inertial_mismatch",
    "entropy_load",
    "frequency_shear",
    "phase_conflict",
    "coupling_stress",
    "boundary_shear",
    "carrier_slippage",
    "eddy_formation",
    "turbulent_debt",
    "release_drag",
)

TURBULENCE_CLASSES = frozenset(
    {
        "productive_transition",
        "boundary_shear",
        "carrier_conflict",
        "scale_mismatch_chatter",
        "self_sealing_loop",
        "fragmentation",
        "release_instability",
        "optimizer_shock",
    }
)

FUTURE_FREEDOM_COMPONENT_NAMES = (
    "task_adaptability",
    "routing_reserve",
    "recovery_capacity",
    "transfer_capacity",
    "perturbation_tolerance",
    "alternative_path_capacity",
)

COUNTERFACTUAL_ARMS = (
    "A0",
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "A6",
    "A7",
    "A8",
)

_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$"
)


class CarrierCoalitionContractError(
    ValueError
):
    """Raised when a carrier-coalition contract is invalid."""


def _require_identifier(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _IDENTIFIER_PATTERN.fullmatch(
        normalized
    ):
        raise CarrierCoalitionContractError(
            f"{field_name} must be a structural identifier"
        )

    return normalized


def _require_positive_integer(
    field_name: str,
    value: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise CarrierCoalitionContractError(
            f"{field_name} must be a positive integer"
        )

    return value


def _require_unit_interval(
    field_name: str,
    value: float,
) -> float:
    try:
        normalized = float(value)
    except Exception as exc:
        raise CarrierCoalitionContractError(
            f"{field_name} must be numeric"
        ) from exc

    if not 0.0 <= normalized <= 1.0:
        raise CarrierCoalitionContractError(
            f"{field_name} must be within [0, 1]"
        )

    return normalized


def _require_identifiers(
    field_name: str,
    values: tuple[str, ...],
    *,
    nonempty: bool = True,
) -> tuple[str, ...]:
    normalized = tuple(
        _require_identifier(
            field_name,
            value,
        )
        for value in values
    )

    if nonempty and not normalized:
        raise CarrierCoalitionContractError(
            f"{field_name} must not be empty"
        )

    if len(set(normalized)) != len(normalized):
        raise CarrierCoalitionContractError(
            f"{field_name} contains duplicates"
        )

    return normalized


def _require_component_vector(
    field_name: str,
    values: tuple[float, ...],
    names: tuple[str, ...],
) -> tuple[float, ...]:
    if len(values) != len(names):
        raise CarrierCoalitionContractError(
            f"{field_name} must contain "
            f"{len(names)} components"
        )

    return tuple(
        _require_unit_interval(
            f"{field_name}.{name}",
            value,
        )
        for name, value in zip(
            names,
            values,
            strict=True,
        )
    )


@dataclass(frozen=True)
class CarrierValidityRelation:
    relation_id: str
    source_member_id: str
    target_member_id: str
    relation_kind: str
    reference_contract_id: str
    evidence_record_ids: tuple[str, ...]
    context_validated: bool
    phase_compatible: bool
    boundary_compatible: bool
    future_consistent: bool
    observer_only: bool = True
    authority_granted: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "relation_id",
            "source_member_id",
            "target_member_id",
            "reference_contract_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if self.relation_kind not in (
            CARRIER_RELATION_KINDS
        ):
            raise CarrierCoalitionContractError(
                "unknown carrier relation kind"
            )

        object.__setattr__(
            self,
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

        if self.observer_only is not True:
            raise CarrierCoalitionContractError(
                "carrier validity must remain observer-only"
            )

        if self.authority_granted is not False:
            raise CarrierCoalitionContractError(
                "carrier validity cannot grant authority"
            )

    @property
    def valid(
        self,
    ) -> bool:
        return all(
            (
                self.context_validated,
                self.phase_compatible,
                self.boundary_compatible,
                self.future_consistent,
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "source_member_id": self.source_member_id,
            "target_member_id": self.target_member_id,
            "relation_kind": self.relation_kind,
            "reference_contract_id": self.reference_contract_id,
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
            "context_validated": self.context_validated,
            "phase_compatible": self.phase_compatible,
            "boundary_compatible": self.boundary_compatible,
            "future_consistent": self.future_consistent,
            "valid": self.valid,
            "observer_only": True,
            "authority_granted": False,
        }


@dataclass(frozen=True)
class ComponentRoleEvidence:
    evidence_id: str
    member_id: str
    reference_contract_id: str
    horizon_steps: int
    role_values: tuple[float, ...]
    evidence_record_ids: tuple[str, ...]
    role_assignment_nonexclusive: bool = True
    calibrated: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "evidence_id",
            "member_id",
            "reference_contract_id",
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
            "horizon_steps",
            _require_positive_integer(
                "horizon_steps",
                self.horizon_steps,
            ),
        )

        object.__setattr__(
            self,
            "role_values",
            _require_component_vector(
                "role_values",
                self.role_values,
                COMPONENT_ROLE_NAMES,
            ),
        )

        object.__setattr__(
            self,
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

        if (
            self.role_assignment_nonexclusive
            is not True
        ):
            raise CarrierCoalitionContractError(
                "component roles must remain nonexclusive"
            )

    @property
    def role_vector(
        self,
    ) -> dict[str, float]:
        return dict(
            zip(
                COMPONENT_ROLE_NAMES,
                self.role_values,
                strict=True,
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "member_id": self.member_id,
            "reference_contract_id": self.reference_contract_id,
            "horizon_steps": self.horizon_steps,
            "role_vector": self.role_vector,
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
            "role_assignment_nonexclusive": True,
            "calibrated": self.calibrated,
        }


@dataclass(frozen=True)
class MultiscaleTurbulenceChannel:
    channel_id: str
    source_organization_id: str
    target_organization_id: str
    reference_contract_id: str
    horizon_steps: int
    component_values: tuple[float, ...]
    turbulence_classes: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]
    component_preserving: bool = True
    collapsed_score_authoritative: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "channel_id",
            "source_organization_id",
            "target_organization_id",
            "reference_contract_id",
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
            "horizon_steps",
            _require_positive_integer(
                "horizon_steps",
                self.horizon_steps,
            ),
        )

        object.__setattr__(
            self,
            "component_values",
            _require_component_vector(
                "component_values",
                self.component_values,
                TURBULENCE_COMPONENT_NAMES,
            ),
        )

        normalized_classes = tuple(
            str(value).strip()
            for value in self.turbulence_classes
        )

        if not normalized_classes:
            raise CarrierCoalitionContractError(
                "turbulence_classes must not be empty"
            )

        if len(set(normalized_classes)) != len(
            normalized_classes
        ):
            raise CarrierCoalitionContractError(
                "turbulence_classes contains duplicates"
            )

        unknown = sorted(
            set(normalized_classes)
            - TURBULENCE_CLASSES
        )

        if unknown:
            raise CarrierCoalitionContractError(
                f"unknown turbulence classes: {unknown!r}"
            )

        object.__setattr__(
            self,
            "turbulence_classes",
            normalized_classes,
        )

        object.__setattr__(
            self,
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

        if self.component_preserving is not True:
            raise CarrierCoalitionContractError(
                "turbulence components must be preserved"
            )

        if (
            self.collapsed_score_authoritative
            is not False
        ):
            raise CarrierCoalitionContractError(
                "a collapsed turbulence score cannot be authoritative"
            )

    @property
    def component_vector(
        self,
    ) -> dict[str, float]:
        return dict(
            zip(
                TURBULENCE_COMPONENT_NAMES,
                self.component_values,
                strict=True,
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "source_organization_id": self.source_organization_id,
            "target_organization_id": self.target_organization_id,
            "reference_contract_id": self.reference_contract_id,
            "horizon_steps": self.horizon_steps,
            "component_vector": self.component_vector,
            "turbulence_classes": list(
                self.turbulence_classes
            ),
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
            "component_preserving": True,
            "collapsed_score_authoritative": False,
        }


@dataclass(frozen=True)
class ScaffoldDependenceEvidence:
    dependence_id: str
    coalition_id: str
    scaffold_member_ids: tuple[str, ...]
    reference_contract_id: str
    observation_horizon: int
    dependence_values: tuple[float, ...]
    support_required_now: bool
    evidence_record_ids: tuple[str, ...]

    DEPENDENCE_COMPONENT_NAMES = (
        "optimizer_shock",
        "activation_shear",
        "gradient_conflict",
        "routing_dependency",
        "boundary_strain",
        "release_drag",
        "delayed_degradation_risk",
    )

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "dependence_id",
            "coalition_id",
            "reference_contract_id",
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
            "scaffold_member_ids",
            _require_identifiers(
                "scaffold_member_ids",
                self.scaffold_member_ids,
            ),
        )

        object.__setattr__(
            self,
            "observation_horizon",
            _require_positive_integer(
                "observation_horizon",
                self.observation_horizon,
            ),
        )

        object.__setattr__(
            self,
            "dependence_values",
            _require_component_vector(
                "dependence_values",
                self.dependence_values,
                self.DEPENDENCE_COMPONENT_NAMES,
            ),
        )

        object.__setattr__(
            self,
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

    @property
    def dependence_vector(
        self,
    ) -> dict[str, float]:
        return dict(
            zip(
                self.DEPENDENCE_COMPONENT_NAMES,
                self.dependence_values,
                strict=True,
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "dependence_id": self.dependence_id,
            "coalition_id": self.coalition_id,
            "scaffold_member_ids": list(
                self.scaffold_member_ids
            ),
            "reference_contract_id": self.reference_contract_id,
            "observation_horizon": self.observation_horizon,
            "dependence_vector": self.dependence_vector,
            "support_required_now": self.support_required_now,
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
        }


@dataclass(frozen=True)
class ScaffoldReleaseEvidence:
    release_id: str
    coalition_id: str
    scaffold_member_ids: tuple[str, ...]
    reference_contract_id: str
    withdrawal_horizon: int
    immediate_performance_preserved: bool
    far_horizon_performance_preserved: bool
    calibration_preserved: bool
    robustness_preserved: bool
    optimizer_stable: bool
    activation_geometry_stable: bool
    persistence_verified: bool
    recovery_available: bool
    transfer_preserved: bool
    future_freedom_preserved: bool
    turbulent_debt_admissible: bool
    evidence_record_ids: tuple[str, ...]
    observer_only: bool = True
    authority_granted: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "release_id",
            "coalition_id",
            "reference_contract_id",
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
            "scaffold_member_ids",
            _require_identifiers(
                "scaffold_member_ids",
                self.scaffold_member_ids,
            ),
        )

        object.__setattr__(
            self,
            "withdrawal_horizon",
            _require_positive_integer(
                "withdrawal_horizon",
                self.withdrawal_horizon,
            ),
        )

        object.__setattr__(
            self,
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

        if self.observer_only is not True:
            raise CarrierCoalitionContractError(
                "release evidence must remain observer-only"
            )

        if self.authority_granted is not False:
            raise CarrierCoalitionContractError(
                "release evidence cannot grant authority"
            )

    @property
    def release_admissible(
        self,
    ) -> bool:
        return all(
            (
                self.immediate_performance_preserved,
                self.far_horizon_performance_preserved,
                self.calibration_preserved,
                self.robustness_preserved,
                self.optimizer_stable,
                self.activation_geometry_stable,
                self.persistence_verified,
                self.recovery_available,
                self.transfer_preserved,
                self.future_freedom_preserved,
                self.turbulent_debt_admissible,
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "coalition_id": self.coalition_id,
            "scaffold_member_ids": list(
                self.scaffold_member_ids
            ),
            "reference_contract_id": self.reference_contract_id,
            "withdrawal_horizon": self.withdrawal_horizon,
            "immediate_performance_preserved": self.immediate_performance_preserved,
            "far_horizon_performance_preserved": self.far_horizon_performance_preserved,
            "calibration_preserved": self.calibration_preserved,
            "robustness_preserved": self.robustness_preserved,
            "optimizer_stable": self.optimizer_stable,
            "activation_geometry_stable": self.activation_geometry_stable,
            "persistence_verified": self.persistence_verified,
            "recovery_available": self.recovery_available,
            "transfer_preserved": self.transfer_preserved,
            "future_freedom_preserved": self.future_freedom_preserved,
            "turbulent_debt_admissible": self.turbulent_debt_admissible,
            "release_admissible": self.release_admissible,
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
            "observer_only": True,
            "authority_granted": False,
        }


@dataclass(frozen=True)
class CompressionFutureFreedom:
    record_id: str
    coalition_id: str
    reference_contract_id: str
    baseline_values: tuple[float, ...]
    candidate_values: tuple[float, ...]
    minimum_retention_ratio: float
    evidence_record_ids: tuple[str, ...]

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "record_id",
            "coalition_id",
            "reference_contract_id",
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
            "baseline_values",
            _require_component_vector(
                "baseline_values",
                self.baseline_values,
                FUTURE_FREEDOM_COMPONENT_NAMES,
            ),
        )

        object.__setattr__(
            self,
            "candidate_values",
            _require_component_vector(
                "candidate_values",
                self.candidate_values,
                FUTURE_FREEDOM_COMPONENT_NAMES,
            ),
        )

        object.__setattr__(
            self,
            "minimum_retention_ratio",
            _require_unit_interval(
                "minimum_retention_ratio",
                self.minimum_retention_ratio,
            ),
        )

        object.__setattr__(
            self,
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

    @property
    def retention_by_component(
        self,
    ) -> dict[str, float]:
        values = {}

        for name, baseline, candidate in zip(
            FUTURE_FREEDOM_COMPONENT_NAMES,
            self.baseline_values,
            self.candidate_values,
            strict=True,
        ):
            if baseline == 0.0:
                ratio = 1.0
            else:
                ratio = min(
                    1.0,
                    candidate / baseline,
                )

            values[name] = ratio

        return values

    @property
    def preserved(
        self,
    ) -> bool:
        return all(
            ratio
            >= self.minimum_retention_ratio
            for ratio in (
                self.retention_by_component.values()
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "coalition_id": self.coalition_id,
            "reference_contract_id": self.reference_contract_id,
            "baseline_vector": dict(
                zip(
                    FUTURE_FREEDOM_COMPONENT_NAMES,
                    self.baseline_values,
                    strict=True,
                )
            ),
            "candidate_vector": dict(
                zip(
                    FUTURE_FREEDOM_COMPONENT_NAMES,
                    self.candidate_values,
                    strict=True,
                )
            ),
            "retention_by_component": self.retention_by_component,
            "minimum_retention_ratio": self.minimum_retention_ratio,
            "preserved": self.preserved,
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
        }


@dataclass(frozen=True)
class TrajectoryAdmissibleCarrierCoalition:
    coalition_id: str
    member_ids: tuple[str, ...]
    initialization_state_id: str
    optimizer_state_id: str
    data_contract_id: str
    reference_contract_id: str
    participation_geometry_id: str
    transformation_geometry_id: str
    boundary_contract_id: str
    terminal_organizational_class_id: str
    scaffold_relation_ids: tuple[str, ...]
    turbulence_channel_ids: tuple[str, ...]
    role_evidence_ids: tuple[str, ...]
    future_freedom_record_id: str
    horizon_steps: int
    forward_reachable: bool
    backward_consistent: bool
    self_supporting: bool
    support_withdrawal_verified: bool
    context_specific: bool
    observer_only: bool = True
    authority_eligible: bool = False
    no_auto_promotion: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "coalition_id",
            "initialization_state_id",
            "optimizer_state_id",
            "data_contract_id",
            "reference_contract_id",
            "participation_geometry_id",
            "transformation_geometry_id",
            "boundary_contract_id",
            "terminal_organizational_class_id",
            "future_freedom_record_id",
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
            "member_ids",
            "scaffold_relation_ids",
            "turbulence_channel_ids",
            "role_evidence_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifiers(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "horizon_steps",
            _require_positive_integer(
                "horizon_steps",
                self.horizon_steps,
            ),
        )

        if self.observer_only is not True:
            raise CarrierCoalitionContractError(
                "coalition must remain observer-only"
            )

        if self.authority_eligible is not False:
            raise CarrierCoalitionContractError(
                "coalition cannot become authority-eligible in R1A"
            )

        if self.no_auto_promotion is not True:
            raise CarrierCoalitionContractError(
                "coalition must prohibit auto-promotion"
            )

    @property
    def nomination_ready(
        self,
    ) -> bool:
        return all(
            (
                self.forward_reachable,
                self.backward_consistent,
                self.self_supporting,
                self.support_withdrawal_verified,
            )
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "coalition_id": self.coalition_id,
            "member_ids": list(
                self.member_ids
            ),
            "initialization_state_id": self.initialization_state_id,
            "optimizer_state_id": self.optimizer_state_id,
            "data_contract_id": self.data_contract_id,
            "reference_contract_id": self.reference_contract_id,
            "participation_geometry_id": self.participation_geometry_id,
            "transformation_geometry_id": self.transformation_geometry_id,
            "boundary_contract_id": self.boundary_contract_id,
            "terminal_organizational_class_id": self.terminal_organizational_class_id,
            "scaffold_relation_ids": list(
                self.scaffold_relation_ids
            ),
            "turbulence_channel_ids": list(
                self.turbulence_channel_ids
            ),
            "role_evidence_ids": list(
                self.role_evidence_ids
            ),
            "future_freedom_record_id": self.future_freedom_record_id,
            "horizon_steps": self.horizon_steps,
            "forward_reachable": self.forward_reachable,
            "backward_consistent": self.backward_consistent,
            "self_supporting": self.self_supporting,
            "support_withdrawal_verified": self.support_withdrawal_verified,
            "context_specific": self.context_specific,
            "nomination_ready": self.nomination_ready,
            "observer_only": True,
            "authority_eligible": False,
            "no_auto_promotion": True,
        }


@dataclass(frozen=True)
class CoalitionCounterfactualBranch:
    branch_id: str
    arm: str
    coalition_id: str
    topology_relation: str
    initialization_relation: str
    context_relation: str
    support_relation: str
    dynamic_topology: bool
    outcome_record_ids: tuple[str, ...]

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "branch_id",
            "coalition_id",
            "topology_relation",
            "initialization_relation",
            "context_relation",
            "support_relation",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if self.arm not in COUNTERFACTUAL_ARMS:
            raise CarrierCoalitionContractError(
                "unknown coalition counterfactual arm"
            )

        object.__setattr__(
            self,
            "outcome_record_ids",
            _require_identifiers(
                "outcome_record_ids",
                self.outcome_record_ids,
            ),
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "arm": self.arm,
            "coalition_id": self.coalition_id,
            "topology_relation": self.topology_relation,
            "initialization_relation": self.initialization_relation,
            "context_relation": self.context_relation,
            "support_relation": self.support_relation,
            "dynamic_topology": self.dynamic_topology,
            "outcome_record_ids": list(
                self.outcome_record_ids
            ),
        }


@dataclass(frozen=True)
class CoalitionMatchedExperiment:
    experiment_id: str
    branches: tuple[
        CoalitionCounterfactualBranch,
        ...,
    ]
    acceptance_contract_id: str
    independent_evaluator: bool
    frozen_acceptance_criteria: bool
    matched_compute_budget: bool
    multiple_seeds: bool
    observer_only: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "experiment_id",
            "acceptance_contract_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        arms = tuple(
            branch.arm
            for branch in self.branches
        )

        if set(arms) != set(
            COUNTERFACTUAL_ARMS
        ):
            raise CarrierCoalitionContractError(
                "matched experiment must contain A0 through A8"
            )

        if len(arms) != len(
            COUNTERFACTUAL_ARMS
        ):
            raise CarrierCoalitionContractError(
                "matched experiment contains duplicate arms"
            )

        branch_ids = tuple(
            branch.branch_id
            for branch in self.branches
        )

        if len(set(branch_ids)) != len(
            branch_ids
        ):
            raise CarrierCoalitionContractError(
                "matched experiment contains duplicate branch IDs"
            )

        if self.independent_evaluator is not True:
            raise CarrierCoalitionContractError(
                "matched experiment requires an independent evaluator"
            )

        if self.frozen_acceptance_criteria is not True:
            raise CarrierCoalitionContractError(
                "matched experiment requires frozen criteria"
            )

        if self.matched_compute_budget is not True:
            raise CarrierCoalitionContractError(
                "matched experiment requires matched compute"
            )

        if self.multiple_seeds is not True:
            raise CarrierCoalitionContractError(
                "matched experiment requires multiple seeds"
            )

        if self.observer_only is not True:
            raise CarrierCoalitionContractError(
                "matched experiment must remain observer-only"
            )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "branches": [
                branch.to_dict()
                for branch in self.branches
            ],
            "acceptance_contract_id": self.acceptance_contract_id,
            "independent_evaluator": True,
            "frozen_acceptance_criteria": True,
            "matched_compute_budget": True,
            "multiple_seeds": True,
            "observer_only": True,
        }


@dataclass(frozen=True)
class MicroscopicTicketEquivalenceClass:
    class_id: str
    coalition_ids: tuple[str, ...]
    terminal_organizational_class_id: str
    invariant_function_record_ids: tuple[str, ...]
    admissible_corridor_ids: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]
    equivalent_terminal_organization: bool = True
    identical_microstate_required: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "class_id",
            "terminal_organizational_class_id",
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
            "coalition_ids",
            "invariant_function_record_ids",
            "admissible_corridor_ids",
            "evidence_record_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifiers(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if len(self.coalition_ids) < 2:
            raise CarrierCoalitionContractError(
                "ticket equivalence requires at least two coalitions"
            )

        if (
            self.equivalent_terminal_organization
            is not True
        ):
            raise CarrierCoalitionContractError(
                "ticket class must share a terminal organization"
            )

        if self.identical_microstate_required is not False:
            raise CarrierCoalitionContractError(
                "ticket equivalence cannot require identical microstates"
            )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "coalition_ids": list(
                self.coalition_ids
            ),
            "terminal_organizational_class_id": self.terminal_organizational_class_id,
            "invariant_function_record_ids": list(
                self.invariant_function_record_ids
            ),
            "admissible_corridor_ids": list(
                self.admissible_corridor_ids
            ),
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
            "equivalent_terminal_organization": True,
            "identical_microstate_required": False,
        }


@dataclass(frozen=True)
class ProspectiveCoalitionNomination:
    nomination_id: str
    coalition_id: str
    retrospective_atlas_id: str
    matched_experiment_id: str
    acceptance_contract_id: str
    evidence_record_ids: tuple[str, ...]
    frozen_criteria: bool = True
    independent_evaluator: bool = True
    reversible: bool = True
    observer_only: bool = True
    authority_withheld: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "nomination_id",
            "coalition_id",
            "retrospective_atlas_id",
            "matched_experiment_id",
            "acceptance_contract_id",
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
            "evidence_record_ids",
            _require_identifiers(
                "evidence_record_ids",
                self.evidence_record_ids,
            ),
        )

        required_true = {
            "frozen_criteria": self.frozen_criteria,
            "independent_evaluator": self.independent_evaluator,
            "reversible": self.reversible,
            "observer_only": self.observer_only,
            "authority_withheld": self.authority_withheld,
        }

        failed = sorted(
            name
            for name, value in required_true.items()
            if value is not True
        )

        if failed:
            raise CarrierCoalitionContractError(
                f"nomination safeguards failed: {failed!r}"
            )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "nomination_id": self.nomination_id,
            "coalition_id": self.coalition_id,
            "retrospective_atlas_id": self.retrospective_atlas_id,
            "matched_experiment_id": self.matched_experiment_id,
            "acceptance_contract_id": self.acceptance_contract_id,
            "evidence_record_ids": list(
                self.evidence_record_ids
            ),
            "frozen_criteria": True,
            "independent_evaluator": True,
            "reversible": True,
            "observer_only": True,
            "authority_withheld": True,
        }


@dataclass(frozen=True)
class ReversiblePruningAuthorityContract:
    contract_id: str
    coalition_id: str
    rollback_checkpoint_id: str
    release_criteria_ids: tuple[str, ...]
    maximum_sparsity: float
    maximum_step_removal: float
    recovery_budget_steps: int
    authority_status: str = "observe_only"
    external_causal_authority: bool = False
    self_modification_authority: bool = False
    no_auto_promotion: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "contract_id",
            "coalition_id",
            "rollback_checkpoint_id",
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
            "release_criteria_ids",
            _require_identifiers(
                "release_criteria_ids",
                self.release_criteria_ids,
            ),
        )

        object.__setattr__(
            self,
            "maximum_sparsity",
            _require_unit_interval(
                "maximum_sparsity",
                self.maximum_sparsity,
            ),
        )

        object.__setattr__(
            self,
            "maximum_step_removal",
            _require_unit_interval(
                "maximum_step_removal",
                self.maximum_step_removal,
            ),
        )

        object.__setattr__(
            self,
            "recovery_budget_steps",
            _require_positive_integer(
                "recovery_budget_steps",
                self.recovery_budget_steps,
            ),
        )

        if self.authority_status != "observe_only":
            raise CarrierCoalitionContractError(
                "R1A authority status must remain observe_only"
            )

        if self.external_causal_authority is not False:
            raise CarrierCoalitionContractError(
                "external causal authority is forbidden"
            )

        if self.self_modification_authority is not False:
            raise CarrierCoalitionContractError(
                "self-modification authority is forbidden"
            )

        if self.no_auto_promotion is not True:
            raise CarrierCoalitionContractError(
                "pruning contract must prohibit auto-promotion"
            )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "coalition_id": self.coalition_id,
            "rollback_checkpoint_id": self.rollback_checkpoint_id,
            "release_criteria_ids": list(
                self.release_criteria_ids
            ),
            "maximum_sparsity": self.maximum_sparsity,
            "maximum_step_removal": self.maximum_step_removal,
            "recovery_budget_steps": self.recovery_budget_steps,
            "authority_status": "observe_only",
            "external_causal_authority": False,
            "self_modification_authority": False,
            "no_auto_promotion": True,
        }


@dataclass(frozen=True)
class RCAFCarrierCoalitionBundle:
    bundle_id: str
    coalition: TrajectoryAdmissibleCarrierCoalition
    validity_relations: tuple[
        CarrierValidityRelation,
        ...,
    ]
    role_evidence: tuple[
        ComponentRoleEvidence,
        ...,
    ]
    scaffold_dependence: ScaffoldDependenceEvidence
    scaffold_release: ScaffoldReleaseEvidence
    turbulence_channels: tuple[
        MultiscaleTurbulenceChannel,
        ...,
    ]
    future_freedom: CompressionFutureFreedom
    matched_experiment: CoalitionMatchedExperiment
    equivalence_class: MicroscopicTicketEquivalenceClass
    nomination: ProspectiveCoalitionNomination
    authority_contract: ReversiblePruningAuthorityContract
    raw_content_stored: bool = False
    content_fingerprint_stored: bool = False

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "bundle_id",
            _require_identifier(
                "bundle_id",
                self.bundle_id,
            ),
        )

        coalition_id = (
            self.coalition.coalition_id
        )

        member_ids = set(
            self.coalition.member_ids
        )

        if not self.validity_relations:
            raise CarrierCoalitionContractError(
                "validity_relations must not be empty"
            )

        if not self.role_evidence:
            raise CarrierCoalitionContractError(
                "role_evidence must not be empty"
            )

        if not self.turbulence_channels:
            raise CarrierCoalitionContractError(
                "turbulence_channels must not be empty"
            )

        for relation in self.validity_relations:
            if (
                relation.source_member_id
                not in member_ids
                or relation.target_member_id
                not in member_ids
            ):
                raise CarrierCoalitionContractError(
                    "carrier relation references a non-member"
                )

        for evidence in self.role_evidence:
            if evidence.member_id not in member_ids:
                raise CarrierCoalitionContractError(
                    "role evidence references a non-member"
                )

        linked_coalition_ids = {
            self.scaffold_dependence.coalition_id,
            self.scaffold_release.coalition_id,
            self.future_freedom.coalition_id,
            self.nomination.coalition_id,
            self.authority_contract.coalition_id,
        }

        linked_coalition_ids.update(
            branch.coalition_id
            for branch in (
                self.matched_experiment.branches
            )
        )

        if linked_coalition_ids != {
            coalition_id
        }:
            raise CarrierCoalitionContractError(
                "bundle contains inconsistent coalition links"
            )

        if coalition_id not in (
            self.equivalence_class.coalition_ids
        ):
            raise CarrierCoalitionContractError(
                "equivalence class omits the primary coalition"
            )

        scaffold_members = set(
            self.scaffold_dependence.scaffold_member_ids
        ) | set(
            self.scaffold_release.scaffold_member_ids
        )

        if not scaffold_members.issubset(
            member_ids
        ):
            raise CarrierCoalitionContractError(
                "scaffold evidence references a non-member"
            )

        if self.raw_content_stored is not False:
            raise CarrierCoalitionContractError(
                "raw content storage is forbidden"
            )

        if (
            self.content_fingerprint_stored
            is not False
        ):
            raise CarrierCoalitionContractError(
                "content fingerprints are forbidden"
            )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "schema": RCAF_CARRIER_COALITION_SCHEMA,
            "bundle_id": self.bundle_id,
            "coalition": self.coalition.to_dict(),
            "validity_relations": [
                relation.to_dict()
                for relation in self.validity_relations
            ],
            "role_evidence": [
                evidence.to_dict()
                for evidence in self.role_evidence
            ],
            "scaffold_dependence": (
                self.scaffold_dependence.to_dict()
            ),
            "scaffold_release": (
                self.scaffold_release.to_dict()
            ),
            "turbulence_channels": [
                channel.to_dict()
                for channel in self.turbulence_channels
            ],
            "future_freedom": (
                self.future_freedom.to_dict()
            ),
            "matched_experiment": (
                self.matched_experiment.to_dict()
            ),
            "equivalence_class": (
                self.equivalence_class.to_dict()
            ),
            "nomination": self.nomination.to_dict(),
            "authority_contract": (
                self.authority_contract.to_dict()
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
