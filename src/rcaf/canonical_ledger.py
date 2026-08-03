# ============================================================
# src/rcaf/canonical_ledger.py
# ============================================================

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.rcaf.future_authority_ledger import (
    CALIBRATION_STATES,
    MATCHED_CAUSAL_BRANCH_ROLES,
    FutureConditionedAuthorityEvidenceBundle,
)

from src.rcaf.carrier_coalition import (
    COMPONENT_ROLE_NAMES,
    COUNTERFACTUAL_ARMS,
    FUTURE_FREEDOM_COMPONENT_NAMES,
    TURBULENCE_COMPONENT_NAMES,
    RCAFCarrierCoalitionBundle,
)

from src.rcaf.organizational_ledger import (
    MetricVector,
    ParticipationGeometry,
    RCAF_ATLAS_TYPES,
    RCAFOrganizationalRecord,
)


RCAF_CANONICAL_LEDGER_SCHEMA = (
    "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.4"
)

RCAF_BROAD_ORGANIZATIONAL_CHAIN = (
    "possibility",
    "pressure",
    "salience",
    "curiosity",
    "participation",
    "mediation",
    "realization",
    "occupancy",
    "persistence",
    "feedback",
    "modified_possibility",
)

RCAF_REALIZATION_GOVERNANCE_CHAIN = (
    "possibility",
    "proposal_gain_discovery",
    "verification",
    "transition_qualification",
    "edge_admissibility",
    "geometry_coherence",
    "transformation_geometry",
    "active_participation_permission",
    "realization_injection",
    "absorption",
    "influence",
    "persistence",
    "release",
    "modified_possibility",
)

RCAF_INTERFACE_PROPAGATION_CHAIN = (
    "presence",
    "expression",
    "coupling",
    "local_realization",
    "propagation",
    "absorption",
    "persistence",
    "global_influence",
)

RCAF_EVENT_COORDINATE_AXES = (
    "T",
    "O",
    "X",
    "Ref",
    "Pi",
    "Gamma",
    "J",
    "Q",
    "B",
    "H",
    "E",
)

RCAF_PROCESS_STATUSES = frozenset(
    {
        "not_observed",
        "possible",
        "proposed",
        "observed",
        "partial",
        "verified",
        "qualified",
        "admissible",
        "authorized",
        "executed",
        "absorbed",
        "influential",
        "persistent",
        "released",
        "revoked",
        "deferred",
        "failed",
        "rejected",
    }
)

REFERENCE_VALIDITY_STATES = frozenset(
    {
        "unknown",
        "provisional",
        "valid",
        "drifting",
        "invalid",
        "released",
        "replaced",
    }
)

TRIADIC_DEFERRAL_STATES = frozenset(
    {
        "not_evaluated",
        "open",
        "deferred",
        "resolved",
        "rejected",
    }
)

GOLDILOCKS_REGIMES = frozenset(
    {
        "stagnant",
        "bounded_explorative",
        "chaotic",
        "transitioning",
        "unknown",
    }
)

POLARITY_REGIMES = frozenset(
    {
        "ordinary",
        "escape",
        "inverse_goldilocks",
        "mixed",
        "unknown",
    }
)

HARMONICS_RETENTION_STATES = frozenset(
    {
        "not_evaluated",
        "retained",
        "partial",
        "lost",
        "rejected",
    }
)

AUTHORITY_LIFECYCLE_STATES = frozenset(
    {
        "none",
        "proposed",
        "permitted",
        "granted",
        "active",
        "expired",
        "released",
        "revoked",
        "denied",
    }
)

COMMITMENT_STATES = frozenset(
    {
        "uncommitted",
        "entering",
        "committed",
        "retained",
        "releasing",
        "released",
        "revoked",
    }
)

AUTHORITY_POSTURES = frozenset(
    {
        "observe_only",
        "sandboxed",
        "bounded",
        "active",
    }
)

RCAF_PRIMITIVE_NAMES = frozenset(
    {
        "coherence_psi",
        "coherence_meta_field",
        "activity_density_rho_a",
        "coherence_density_rho_c",
        "salience",
        "realization_pressure_lambda",
        "curiosity",
        "viability_omega",
        "occupancy_xi",
        "future_freedom_f_xi",
        "realization_coupling_kappa_r",
        "absorption_a_r_h",
        "mediation",
        "feedback",
    }
)

RCAF_CANONICAL_CAPABILITIES = frozenset(
    {
        "canonical_primitives",
        "broad_organizational_chain",
        "realization_governance_chain",
        "reference_conditioning",
        "triadic_participation",
        "participation_corridor",
        "goldilocks_regulation",
        "harmonics_retention",
        "proposal_gain_discovery",
        "verification_evidence",
        "boundary_admissibility",
        "transformation_geometry",
        "multi_space_coordinates",
        "future_conditioned_authority",
        "authority_lifecycle",
        "interface_propagation_distinctions",
        "realization_absorption_persistence_release",
        "counterfactual_comparison",
        "canonical_multi_atlas",
        "e8_reference_geometry",
        "leech_reference_geometry",
        "monster_meta_atlas",
        "causal_cone_reference",
        "operator_flow_reference",
        "minimal_event_coordinate",
        "commitment_hysteresis",
        "primitive_derived_separation",
        "layered_decision_trace",
        "observer_effects_linkage",
        "observer_lineage_without_identity_claim",
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
        "privacy_boundary",
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
)

_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$"
)


class RCAFCanonicalLedgerError(ValueError):
    """Raised when a canonical RCAF contract is invalid."""


def _require_identifier(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise RCAFCanonicalLedgerError(
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
        raise RCAFCanonicalLedgerError(
            f"{field_name} must be one of {sorted(allowed)!r}; "
            f"received {normalized!r}"
        )

    return normalized


def _require_boolean(
    field_name: str,
    value: bool,
) -> bool:
    if not isinstance(value, bool):
        raise RCAFCanonicalLedgerError(
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
        raise RCAFCanonicalLedgerError(
            f"{field_name} must be a non-negative integer"
        )

    return value


def _require_utc_timestamp(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    try:
        parsed = datetime.fromisoformat(
            normalized.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as exc:
        raise RCAFCanonicalLedgerError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc

    if (
        parsed.tzinfo is None
        or parsed.utcoffset()
        != timezone.utc.utcoffset(parsed)
    ):
        raise RCAFCanonicalLedgerError(
            f"{field_name} must be UTC"
        )

    return normalized


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
        raise RCAFCanonicalLedgerError(
            f"{field_name} must not be empty"
        )

    if len(set(normalized)) != len(normalized):
        raise RCAFCanonicalLedgerError(
            f"{field_name} contains duplicate identifiers"
        )

    return normalized


def _require_vector(
    field_name: str,
    value: MetricVector,
) -> MetricVector:
    if not isinstance(value, MetricVector):
        raise RCAFCanonicalLedgerError(
            f"{field_name} must be MetricVector"
        )

    return value


@dataclass(frozen=True)
class HorizonMetric:
    horizon: int
    values: MetricVector

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "horizon",
            _require_nonnegative_integer(
                "horizon",
                self.horizon,
            ),
        )

        object.__setattr__(
            self,
            "values",
            _require_vector(
                "values",
                self.values,
            ),
        )

        if not self.values.components:
            raise RCAFCanonicalLedgerError(
                "horizon metric values must not be empty"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "horizon": self.horizon,
            "values": self.values.to_dict(),
        }


@dataclass(frozen=True)
class ReferenceConditioning:
    reference_contract_id: str
    reference_point_id: str
    reference_class_id: str
    anchor_id: str | None
    validity_state: str
    reference_age_steps: int
    observable_ids: tuple[str, ...]
    drift: MetricVector = field(
        default_factory=MetricVector
    )
    deviation: MetricVector = field(
        default_factory=MetricVector
    )
    uncertainty: MetricVector = field(
        default_factory=MetricVector
    )
    future_freedom: MetricVector = field(
        default_factory=MetricVector
    )
    replacement_condition_ids: tuple[str, ...] = ()
    release_condition_ids: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "reference_contract_id",
            "reference_point_id",
            "reference_class_id",
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
            "anchor_id",
            _optional_identifier(
                "anchor_id",
                self.anchor_id,
            ),
        )

        object.__setattr__(
            self,
            "validity_state",
            _require_choice(
                "validity_state",
                self.validity_state,
                REFERENCE_VALIDITY_STATES,
            ),
        )

        object.__setattr__(
            self,
            "reference_age_steps",
            _require_nonnegative_integer(
                "reference_age_steps",
                self.reference_age_steps,
            ),
        )

        for field_name in (
            "observable_ids",
            "replacement_condition_ids",
            "release_condition_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=(
                        field_name == "observable_ids"
                    ),
                ),
            )

        for field_name in (
            "drift",
            "deviation",
            "uncertainty",
            "future_freedom",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                ),
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_contract_id": self.reference_contract_id,
            "reference_point_id": self.reference_point_id,
            "reference_class_id": self.reference_class_id,
            "anchor_id": self.anchor_id,
            "validity_state": self.validity_state,
            "reference_age_steps": self.reference_age_steps,
            "observable_ids": list(self.observable_ids),
            "drift": self.drift.to_dict(),
            "deviation": self.deviation.to_dict(),
            "uncertainty": self.uncertainty.to_dict(),
            "future_freedom": self.future_freedom.to_dict(),
            "replacement_condition_ids": list(
                self.replacement_condition_ids
            ),
            "release_condition_ids": list(
                self.release_condition_ids
            ),
        }


@dataclass(frozen=True)
class PrimitiveObservableBundle:
    reference_contract_id: str
    coherence_psi: MetricVector
    coherence_meta_field: MetricVector
    activity_density_rho_a: MetricVector
    coherence_density_rho_c: MetricVector
    salience: MetricVector
    realization_pressure_lambda: MetricVector
    curiosity: MetricVector
    viability_omega: MetricVector
    occupancy_xi: MetricVector
    future_freedom_f_xi: MetricVector
    realization_coupling_kappa_r: MetricVector
    absorption_a_r_h: tuple[HorizonMetric, ...]
    mediation: MetricVector
    feedback: MetricVector

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "reference_contract_id",
            _require_identifier(
                "reference_contract_id",
                self.reference_contract_id,
            ),
        )

        for field_name in (
            "coherence_psi",
            "coherence_meta_field",
            "activity_density_rho_a",
            "coherence_density_rho_c",
            "salience",
            "realization_pressure_lambda",
            "curiosity",
            "viability_omega",
            "occupancy_xi",
            "future_freedom_f_xi",
            "realization_coupling_kappa_r",
            "mediation",
            "feedback",
        ):
            vector = _require_vector(
                field_name,
                getattr(self, field_name),
            )

            if not vector.components:
                raise RCAFCanonicalLedgerError(
                    f"{field_name} must not be empty"
                )

            object.__setattr__(
                self,
                field_name,
                vector,
            )

        horizons = tuple(
            self.absorption_a_r_h
        )

        if not horizons:
            raise RCAFCanonicalLedgerError(
                "absorption_a_r_h must not be empty"
            )

        horizon_values = [
            item.horizon
            for item in horizons
        ]

        if len(set(horizon_values)) != len(horizon_values):
            raise RCAFCanonicalLedgerError(
                "absorption_a_r_h contains duplicate horizons"
            )

        object.__setattr__(
            self,
            "absorption_a_r_h",
            tuple(
                sorted(
                    horizons,
                    key=lambda item: item.horizon,
                )
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_contract_id": self.reference_contract_id,
            "coherence_psi": self.coherence_psi.to_dict(),
            "coherence_meta_field": (
                self.coherence_meta_field.to_dict()
            ),
            "activity_density_rho_a": (
                self.activity_density_rho_a.to_dict()
            ),
            "coherence_density_rho_c": (
                self.coherence_density_rho_c.to_dict()
            ),
            "salience": self.salience.to_dict(),
            "realization_pressure_lambda": (
                self.realization_pressure_lambda.to_dict()
            ),
            "curiosity": self.curiosity.to_dict(),
            "viability_omega": self.viability_omega.to_dict(),
            "occupancy_xi": self.occupancy_xi.to_dict(),
            "future_freedom_f_xi": (
                self.future_freedom_f_xi.to_dict()
            ),
            "realization_coupling_kappa_r": (
                self.realization_coupling_kappa_r.to_dict()
            ),
            "absorption_a_r_h": [
                item.to_dict()
                for item in self.absorption_a_r_h
            ],
            "mediation": self.mediation.to_dict(),
            "feedback": self.feedback.to_dict(),
        }


@dataclass(frozen=True)
class ParticipationCorridor:
    participation: ParticipationGeometry
    selected_structure_ids: tuple[str, ...]
    selected_direction_ids: tuple[str, ...]
    corridor_id: str
    triadic_deferral_state: str
    goldilocks_regime: str
    polarity_regime: str
    harmonics_retention_state: str
    transition_evidence_ids: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        if not isinstance(
            self.participation,
            ParticipationGeometry,
        ):
            raise RCAFCanonicalLedgerError(
                "participation must be ParticipationGeometry"
            )

        for field_name in (
            "selected_structure_ids",
            "selected_direction_ids",
            "transition_evidence_ids",
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
                            "selected_structure_ids",
                            "selected_direction_ids",
                        }
                    ),
                ),
            )

        object.__setattr__(
            self,
            "corridor_id",
            _require_identifier(
                "corridor_id",
                self.corridor_id,
            ),
        )

        object.__setattr__(
            self,
            "triadic_deferral_state",
            _require_choice(
                "triadic_deferral_state",
                self.triadic_deferral_state,
                TRIADIC_DEFERRAL_STATES,
            ),
        )

        object.__setattr__(
            self,
            "goldilocks_regime",
            _require_choice(
                "goldilocks_regime",
                self.goldilocks_regime,
                GOLDILOCKS_REGIMES,
            ),
        )

        object.__setattr__(
            self,
            "polarity_regime",
            _require_choice(
                "polarity_regime",
                self.polarity_regime,
                POLARITY_REGIMES,
            ),
        )

        object.__setattr__(
            self,
            "harmonics_retention_state",
            _require_choice(
                "harmonics_retention_state",
                self.harmonics_retention_state,
                HARMONICS_RETENTION_STATES,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "participation": self.participation.to_dict(),
            "selected_structure_ids": list(
                self.selected_structure_ids
            ),
            "selected_direction_ids": list(
                self.selected_direction_ids
            ),
            "corridor_id": self.corridor_id,
            "triadic_deferral_state": self.triadic_deferral_state,
            "goldilocks_regime": self.goldilocks_regime,
            "polarity_regime": self.polarity_regime,
            "harmonics_retention_state": (
                self.harmonics_retention_state
            ),
            "transition_evidence_ids": list(
                self.transition_evidence_ids
            ),
        }


@dataclass(frozen=True)
class StageObservation:
    stage: str
    status: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "stage",
            _require_identifier(
                "stage",
                self.stage,
            ),
        )

        object.__setattr__(
            self,
            "status",
            _require_choice(
                "status",
                self.status,
                RCAF_PROCESS_STATUSES,
            ),
        )

        object.__setattr__(
            self,
            "evidence_ids",
            _normalize_identifiers(
                "evidence_ids",
                self.evidence_ids,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ProcessChainObservation:
    broad_chain: tuple[StageObservation, ...]
    realization_governance_chain: tuple[
        StageObservation,
        ...,
    ]

    def __post_init__(
        self,
    ) -> None:
        broad_stages = tuple(
            item.stage
            for item in self.broad_chain
        )

        governance_stages = tuple(
            item.stage
            for item in self.realization_governance_chain
        )

        if broad_stages != RCAF_BROAD_ORGANIZATIONAL_CHAIN:
            raise RCAFCanonicalLedgerError(
                "broad_chain must preserve the canonical order"
            )

        if (
            governance_stages
            != RCAF_REALIZATION_GOVERNANCE_CHAIN
        ):
            raise RCAFCanonicalLedgerError(
                "realization_governance_chain must preserve "
                "the canonical order"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "broad_chain": [
                item.to_dict()
                for item in self.broad_chain
            ],
            "realization_governance_chain": [
                item.to_dict()
                for item
                in self.realization_governance_chain
            ],
        }


@dataclass(frozen=True)
class ProposalGainDiscovery:
    candidate_ids: tuple[str, ...]
    selected_candidate_id: str | None
    gain_components: MetricVector
    discovery_components: MetricVector
    evidence_ids: tuple[str, ...] = ()
    assumption_ids: tuple[str, ...] = ()
    limitation_ids: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        candidates = _normalize_identifiers(
            "candidate_ids",
            self.candidate_ids,
            require_nonempty=True,
        )

        object.__setattr__(
            self,
            "candidate_ids",
            candidates,
        )

        selected = _optional_identifier(
            "selected_candidate_id",
            self.selected_candidate_id,
        )

        if (
            selected is not None
            and selected not in candidates
        ):
            raise RCAFCanonicalLedgerError(
                "selected_candidate_id must occur in candidate_ids"
            )

        object.__setattr__(
            self,
            "selected_candidate_id",
            selected,
        )

        for field_name in (
            "gain_components",
            "discovery_components",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "evidence_ids",
            "assumption_ids",
            "limitation_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                ),
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "candidate_ids": list(self.candidate_ids),
            "selected_candidate_id": self.selected_candidate_id,
            "gain_components": self.gain_components.to_dict(),
            "discovery_components": (
                self.discovery_components.to_dict()
            ),
            "evidence_ids": list(self.evidence_ids),
            "assumption_ids": list(self.assumption_ids),
            "limitation_ids": list(self.limitation_ids),
        }


@dataclass(frozen=True)
class TransformationGeometryRecord:
    event_state_space_id: str
    reference_space_id: str
    proposal_tangent_space_id: str
    causal_cone_space_id: str
    response_parameter_space_id: str
    outcome_composition_space_id: str
    invariant_quotient_space_id: str
    interface_coupling_space_id: str
    propagation_path_space_id: str
    operator_semigroup_space_id: str
    evidence_status_space_id: str
    meta_atlas_space_id: str
    geometry_components: MetricVector

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "event_state_space_id",
            "reference_space_id",
            "proposal_tangent_space_id",
            "causal_cone_space_id",
            "response_parameter_space_id",
            "outcome_composition_space_id",
            "invariant_quotient_space_id",
            "interface_coupling_space_id",
            "propagation_path_space_id",
            "operator_semigroup_space_id",
            "evidence_status_space_id",
            "meta_atlas_space_id",
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
            "geometry_components",
            _require_vector(
                "geometry_components",
                self.geometry_components,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            field_name: getattr(
                self,
                field_name,
            )
            for field_name in (
                "event_state_space_id",
                "reference_space_id",
                "proposal_tangent_space_id",
                "causal_cone_space_id",
                "response_parameter_space_id",
                "outcome_composition_space_id",
                "invariant_quotient_space_id",
                "interface_coupling_space_id",
                "propagation_path_space_id",
                "operator_semigroup_space_id",
                "evidence_status_space_id",
                "meta_atlas_space_id",
            )
        } | {
            "geometry_components": (
                self.geometry_components.to_dict()
            ),
        }


@dataclass(frozen=True)
class InterfaceStageObservation:
    stage: str
    status: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "stage",
            _require_identifier(
                "stage",
                self.stage,
            ),
        )

        object.__setattr__(
            self,
            "status",
            _require_choice(
                "status",
                self.status,
                RCAF_PROCESS_STATUSES,
            ),
        )

        object.__setattr__(
            self,
            "evidence_ids",
            _normalize_identifiers(
                "evidence_ids",
                self.evidence_ids,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class InterfacePropagationChain:
    observations: tuple[
        InterfaceStageObservation,
        ...,
    ]

    def __post_init__(
        self,
    ) -> None:
        stages = tuple(
            item.stage
            for item in self.observations
        )

        if stages != RCAF_INTERFACE_PROPAGATION_CHAIN:
            raise RCAFCanonicalLedgerError(
                "interface propagation stages must preserve "
                "the canonical distinction chain"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "observations": [
                item.to_dict()
                for item in self.observations
            ],
        }


@dataclass(frozen=True)
class FutureConditionedAuthorityGate:
    current_state_id: str
    horizon: int
    future_contract_id: str
    terminal_organizational_class_id: str
    forward_reachable_set_id: str
    backward_predecessor_set_id: str
    admissible_corridor_id: str
    trajectory_ids: tuple[str, ...]
    failure_mode_ids: tuple[str, ...]
    forward_reachable: bool
    backward_consistent: bool
    gate_admissible: bool
    uncertainty: MetricVector
    future_freedom_cost: MetricVector
    reversibility: MetricVector

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "current_state_id",
            "future_contract_id",
            "terminal_organizational_class_id",
            "forward_reachable_set_id",
            "backward_predecessor_set_id",
            "admissible_corridor_id",
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
            "horizon",
            _require_nonnegative_integer(
                "horizon",
                self.horizon,
            ),
        )

        for field_name in (
            "trajectory_ids",
            "failure_mode_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                    require_nonempty=(
                        field_name == "trajectory_ids"
                    ),
                ),
            )

        for field_name in (
            "forward_reachable",
            "backward_consistent",
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

        if (
            self.gate_admissible
            and not (
                self.forward_reachable
                and self.backward_consistent
            )
        ):
            raise RCAFCanonicalLedgerError(
                "gate_admissible requires forward reachability "
                "and backward consistency"
            )

        for field_name in (
            "uncertainty",
            "future_freedom_cost",
            "reversibility",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_vector(
                    field_name,
                    getattr(self, field_name),
                ),
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "current_state_id": self.current_state_id,
            "horizon": self.horizon,
            "future_contract_id": self.future_contract_id,
            "terminal_organizational_class_id": (
                self.terminal_organizational_class_id
            ),
            "forward_reachable_set_id": (
                self.forward_reachable_set_id
            ),
            "backward_predecessor_set_id": (
                self.backward_predecessor_set_id
            ),
            "admissible_corridor_id": (
                self.admissible_corridor_id
            ),
            "trajectory_ids": list(self.trajectory_ids),
            "failure_mode_ids": list(self.failure_mode_ids),
            "forward_reachable": self.forward_reachable,
            "backward_consistent": self.backward_consistent,
            "gate_admissible": self.gate_admissible,
            "uncertainty": self.uncertainty.to_dict(),
            "future_freedom_cost": (
                self.future_freedom_cost.to_dict()
            ),
            "reversibility": self.reversibility.to_dict(),
        }


@dataclass(frozen=True)
class AuthorityLifecycleContract:
    status: str
    recipient_id: str
    operation_id: str
    proposal_id: str
    permission_id: str | None = None
    grant_id: str | None = None
    authority_id: str | None = None
    scope_ids: tuple[str, ...] = ()
    boundary_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    future_corridor_id: str | None = None
    containment_requirement_ids: tuple[str, ...] = ()
    rollback_mechanism_id: str | None = None
    release_condition_ids: tuple[str, ...] = ()
    revocation_condition_ids: tuple[str, ...] = ()
    duration_steps: int = 0
    activated: bool = False
    execution_performed: bool = False
    verified_success: bool = False
    persistence_observed: bool = False
    residual_effects: MetricVector = field(
        default_factory=MetricVector
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "status",
            _require_choice(
                "status",
                self.status,
                AUTHORITY_LIFECYCLE_STATES,
            ),
        )

        for field_name in (
            "recipient_id",
            "operation_id",
            "proposal_id",
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
            "permission_id",
            "grant_id",
            "authority_id",
            "future_corridor_id",
            "rollback_mechanism_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        for field_name in (
            "scope_ids",
            "boundary_ids",
            "evidence_ids",
            "containment_requirement_ids",
            "release_condition_ids",
            "revocation_condition_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_identifiers(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "duration_steps",
            _require_nonnegative_integer(
                "duration_steps",
                self.duration_steps,
            ),
        )

        for field_name in (
            "activated",
            "execution_performed",
            "verified_success",
            "persistence_observed",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_boolean(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if (
            self.verified_success
            and not self.execution_performed
        ):
            raise RCAFCanonicalLedgerError(
                "verified_success requires execution_performed"
            )

        if (
            self.persistence_observed
            and not self.verified_success
        ):
            raise RCAFCanonicalLedgerError(
                "persistence_observed requires verified_success"
            )

        object.__setattr__(
            self,
            "residual_effects",
            _require_vector(
                "residual_effects",
                self.residual_effects,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "status": self.status,
            "recipient_id": self.recipient_id,
            "operation_id": self.operation_id,
            "proposal_id": self.proposal_id,
            "permission_id": self.permission_id,
            "grant_id": self.grant_id,
            "authority_id": self.authority_id,
            "scope_ids": list(self.scope_ids),
            "boundary_ids": list(self.boundary_ids),
            "evidence_ids": list(self.evidence_ids),
            "future_corridor_id": self.future_corridor_id,
            "containment_requirement_ids": list(
                self.containment_requirement_ids
            ),
            "rollback_mechanism_id": (
                self.rollback_mechanism_id
            ),
            "release_condition_ids": list(
                self.release_condition_ids
            ),
            "revocation_condition_ids": list(
                self.revocation_condition_ids
            ),
            "duration_steps": self.duration_steps,
            "activated": self.activated,
            "execution_performed": self.execution_performed,
            "verified_success": self.verified_success,
            "persistence_observed": self.persistence_observed,
            "residual_effects": self.residual_effects.to_dict(),
        }


def _forbid_governing_gate(
    governing_gate: bool,
) -> None:
    if governing_gate is not False:
        raise RCAFCanonicalLedgerError(
            "reference geometry cannot be a governing gate"
        )


@dataclass(frozen=True)
class E8LocalHarmonicReference:
    chart_id: str
    nearest_root_basin_id: str
    root_margin: MetricVector
    root_decisiveness: MetricVector
    harmonics_retention: MetricVector
    governing_gate: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "chart_id",
            "nearest_root_basin_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        _forbid_governing_gate(
            self.governing_gate
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_family": "E8",
            "chart_id": self.chart_id,
            "nearest_root_basin_id": (
                self.nearest_root_basin_id
            ),
            "root_margin": self.root_margin.to_dict(),
            "root_decisiveness": (
                self.root_decisiveness.to_dict()
            ),
            "harmonics_retention": (
                self.harmonics_retention.to_dict()
            ),
            "governing_gate": False,
        }


@dataclass(frozen=True)
class LeechGlobalCompatibilityReference:
    atlas_id: str
    compatibility: MetricVector
    packing: MetricVector
    gluing: MetricVector
    rootless_silence: MetricVector
    governing_gate: bool = False

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "atlas_id",
            _require_identifier(
                "atlas_id",
                self.atlas_id,
            ),
        )

        _forbid_governing_gate(
            self.governing_gate
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_family": "LeechLike",
            "atlas_id": self.atlas_id,
            "compatibility": self.compatibility.to_dict(),
            "packing": self.packing.to_dict(),
            "gluing": self.gluing.to_dict(),
            "rootless_silence": (
                self.rootless_silence.to_dict()
            ),
            "governing_gate": False,
        }


@dataclass(frozen=True)
class MonsterMetaAtlasReference:
    meta_atlas_id: str
    transition_flow_atlas_ids: tuple[str, ...]
    symmetry_relation_ids: tuple[str, ...]
    governing_gate: bool = False

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "meta_atlas_id",
            _require_identifier(
                "meta_atlas_id",
                self.meta_atlas_id,
            ),
        )

        for field_name in (
            "transition_flow_atlas_ids",
            "symmetry_relation_ids",
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

        _forbid_governing_gate(
            self.governing_gate
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_family": "MonsterMoonshine",
            "meta_atlas_id": self.meta_atlas_id,
            "transition_flow_atlas_ids": list(
                self.transition_flow_atlas_ids
            ),
            "symmetry_relation_ids": list(
                self.symmetry_relation_ids
            ),
            "governing_gate": False,
        }


@dataclass(frozen=True)
class CausalConeReference:
    causal_cone_id: str
    admissible_direction_ids: tuple[str, ...]
    interval_geometry: MetricVector
    governing_gate: bool = False

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "causal_cone_id",
            _require_identifier(
                "causal_cone_id",
                self.causal_cone_id,
            ),
        )

        object.__setattr__(
            self,
            "admissible_direction_ids",
            _normalize_identifiers(
                "admissible_direction_ids",
                self.admissible_direction_ids,
                require_nonempty=True,
            ),
        )

        _forbid_governing_gate(
            self.governing_gate
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_family": "MinkowskiLorentzian",
            "causal_cone_id": self.causal_cone_id,
            "admissible_direction_ids": list(
                self.admissible_direction_ids
            ),
            "interval_geometry": (
                self.interval_geometry.to_dict()
            ),
            "governing_gate": False,
        }


@dataclass(frozen=True)
class OperatorFlowReference:
    operator_id: str
    semigroup_id: str
    generator_id: str
    flow_metrics: MetricVector
    governing_gate: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "operator_id",
            "semigroup_id",
            "generator_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        _forbid_governing_gate(
            self.governing_gate
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_family": "RemizovChernoff",
            "operator_id": self.operator_id,
            "semigroup_id": self.semigroup_id,
            "generator_id": self.generator_id,
            "flow_metrics": self.flow_metrics.to_dict(),
            "governing_gate": False,
        }


@dataclass(frozen=True)
class GeometricReferenceBundle:
    e8: E8LocalHarmonicReference
    leech: LeechGlobalCompatibilityReference
    monster: MonsterMetaAtlasReference
    causal_cone: CausalConeReference
    operator_flow: OperatorFlowReference

    def to_dict(
        self,
    ) -> dict:
        return {
            "e8": self.e8.to_dict(),
            "leech": self.leech.to_dict(),
            "monster": self.monster.to_dict(),
            "causal_cone": self.causal_cone.to_dict(),
            "operator_flow": self.operator_flow.to_dict(),
        }


@dataclass(frozen=True)
class CommitmentHysteresisRecord:
    corridor_id: str
    reference_contract_id: str
    state: str
    entry_threshold: MetricVector
    retention_threshold: MetricVector
    release_threshold: MetricVector
    future_freedom_remaining: MetricVector
    indecision_avoidance_active: bool

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "corridor_id",
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
            "state",
            _require_choice(
                "state",
                self.state,
                COMMITMENT_STATES,
            ),
        )

        object.__setattr__(
            self,
            "indecision_avoidance_active",
            _require_boolean(
                "indecision_avoidance_active",
                self.indecision_avoidance_active,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "corridor_id": self.corridor_id,
            "reference_contract_id": self.reference_contract_id,
            "state": self.state,
            "entry_threshold": self.entry_threshold.to_dict(),
            "retention_threshold": (
                self.retention_threshold.to_dict()
            ),
            "release_threshold": (
                self.release_threshold.to_dict()
            ),
            "future_freedom_remaining": (
                self.future_freedom_remaining.to_dict()
            ),
            "indecision_avoidance_active": (
                self.indecision_avoidance_active
            ),
        }


@dataclass(frozen=True)
class MinimalEventCoordinate:
    T: str
    O: str
    X: str
    Ref: str
    Pi: str
    Gamma: str
    J: str
    Q: str
    B: str
    H: str
    E: str

    def __post_init__(
        self,
    ) -> None:
        for axis in RCAF_EVENT_COORDINATE_AXES:
            object.__setattr__(
                self,
                axis,
                _require_identifier(
                    axis,
                    getattr(self, axis),
                ),
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            axis: getattr(
                self,
                axis,
            )
            for axis in RCAF_EVENT_COORDINATE_AXES
        }


@dataclass(frozen=True)
class ConditionedDecisionTrace:
    component_observable_ids: tuple[str, ...]
    intermediate_geometry_ids: tuple[str, ...]
    conditioned_decision_ids: tuple[str, ...]
    order_preserved: bool = True

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "component_observable_ids",
            "intermediate_geometry_ids",
            "conditioned_decision_ids",
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

        if self.order_preserved is not True:
            raise RCAFCanonicalLedgerError(
                "component-to-geometry-to-decision order "
                "must remain preserved"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "component_observable_ids": list(
                self.component_observable_ids
            ),
            "intermediate_geometry_ids": list(
                self.intermediate_geometry_ids
            ),
            "conditioned_decision_ids": list(
                self.conditioned_decision_ids
            ),
            "order_preserved": True,
        }


@dataclass(frozen=True)
class DerivedDiagnosticRecord:
    diagnostic_name: str
    values: MetricVector
    derived_from_component_ids: tuple[str, ...]

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "diagnostic_name",
            _require_identifier(
                "diagnostic_name",
                self.diagnostic_name,
            ),
        )

        if self.diagnostic_name in RCAF_PRIMITIVE_NAMES:
            raise RCAFCanonicalLedgerError(
                "primitive observables cannot be stored as "
                "derived diagnostics"
            )

        object.__setattr__(
            self,
            "derived_from_component_ids",
            _normalize_identifiers(
                "derived_from_component_ids",
                self.derived_from_component_ids,
                require_nonempty=True,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "diagnostic_name": self.diagnostic_name,
            "values": self.values.to_dict(),
            "derived_from_component_ids": list(
                self.derived_from_component_ids
            ),
        }


@dataclass(frozen=True)
class RCAFFullFrameworkRecord:
    record_id: str
    event_spine_id: str
    lineage_id: str
    occurred_at_utc: str
    observer_lineage_id: str
    observer_role_id: str
    observer_effects_record_id: str
    organizational_record: RCAFOrganizationalRecord
    reference_conditioning: ReferenceConditioning
    primitives: PrimitiveObservableBundle
    participation: ParticipationCorridor
    process_chains: ProcessChainObservation
    proposal_gain: ProposalGainDiscovery
    transformation_geometry: TransformationGeometryRecord
    interface_propagation: InterfacePropagationChain
    future_gate: FutureConditionedAuthorityGate
    future_authority_evidence: (
        FutureConditionedAuthorityEvidenceBundle
    )
    carrier_coalition: RCAFCarrierCoalitionBundle
    authority_lifecycle: AuthorityLifecycleContract
    geometric_references: GeometricReferenceBundle
    commitment: CommitmentHysteresisRecord
    event_coordinate: MinimalEventCoordinate
    decision_trace: ConditionedDecisionTrace
    derived_diagnostics: tuple[
        DerivedDiagnosticRecord,
        ...,
    ]
    authority_posture: str = "observe_only"
    semantic_memory_authority: bool = False
    identity_proof_established: bool = False
    realization_authorized: bool = False
    external_causal_authority: bool = False
    self_modification_authority: bool = False
    raw_content_stored: bool = False
    content_fingerprint_stored: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "record_id",
            "event_spine_id",
            "lineage_id",
            "observer_lineage_id",
            "observer_role_id",
            "observer_effects_record_id",
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
            "occurred_at_utc",
            _require_utc_timestamp(
                "occurred_at_utc",
                self.occurred_at_utc,
            ),
        )

        object.__setattr__(
            self,
            "authority_posture",
            _require_choice(
                "authority_posture",
                self.authority_posture,
                AUTHORITY_POSTURES,
            ),
        )

        if not isinstance(
            self.organizational_record,
            RCAFOrganizationalRecord,
        ):
            raise RCAFCanonicalLedgerError(
                "organizational_record must be "
                "RCAFOrganizationalRecord"
            )

        atlas_types = {
            link.atlas_type
            for link
            in self.organizational_record.atlas_links
        }

        if atlas_types != RCAF_ATLAS_TYPES:
            raise RCAFCanonicalLedgerError(
                "organizational_record must link every "
                "canonical RCAF atlas type"
            )

        if (
            self.primitives.reference_contract_id
            != self.reference_conditioning.reference_contract_id
        ):
            raise RCAFCanonicalLedgerError(
                "primitive observables must be explicitly "
                "conditioned on the active reference contract"
            )

        if not isinstance(
            self.future_authority_evidence,
            FutureConditionedAuthorityEvidenceBundle,
        ):
            raise RCAFCanonicalLedgerError(
                "future_authority_evidence must be "
                "FutureConditionedAuthorityEvidenceBundle"
            )

        bundle = self.future_authority_evidence

        if not isinstance(
            self.carrier_coalition,
            RCAFCarrierCoalitionBundle,
        ):
            raise RCAFCanonicalLedgerError(
                "carrier_coalition must be "
                "RCAFCarrierCoalitionBundle"
            )

        carrier_bundle = self.carrier_coalition
        carrier = carrier_bundle.coalition

        if (
            carrier.reference_contract_id
            != self.reference_conditioning.reference_contract_id
        ):
            raise RCAFCanonicalLedgerError(
                "carrier coalition is conditioned on "
                "the wrong reference contract"
            )

        if (
            carrier.terminal_organizational_class_id
            != self.future_gate.terminal_organizational_class_id
        ):
            raise RCAFCanonicalLedgerError(
                "carrier coalition terminal organizational "
                "class mismatch"
            )

        if (
            carrier_bundle.scaffold_dependence.reference_contract_id
            != self.reference_conditioning.reference_contract_id
            or carrier_bundle.scaffold_release.reference_contract_id
            != self.reference_conditioning.reference_contract_id
            or carrier_bundle.future_freedom.reference_contract_id
            != self.reference_conditioning.reference_contract_id
        ):
            raise RCAFCanonicalLedgerError(
                "carrier evidence is conditioned on "
                "the wrong reference contract"
            )

        if (
            carrier_bundle.nomination.matched_experiment_id
            != carrier_bundle.matched_experiment.experiment_id
        ):
            raise RCAFCanonicalLedgerError(
                "carrier nomination experiment linkage mismatch"
            )

        if (
            carrier_bundle.nomination.acceptance_contract_id
            != carrier_bundle.matched_experiment.acceptance_contract_id
        ):
            raise RCAFCanonicalLedgerError(
                "carrier nomination acceptance-contract mismatch"
            )

        if (
            carrier_bundle.equivalence_class
            .terminal_organizational_class_id
            != self.future_gate.terminal_organizational_class_id
        ):
            raise RCAFCanonicalLedgerError(
                "ticket equivalence terminal-class mismatch"
            )

        if (
            carrier_bundle.authority_contract.authority_status
            != "observe_only"
            or carrier_bundle.authority_contract
            .external_causal_authority
            is not False
            or carrier_bundle.authority_contract
            .self_modification_authority
            is not False
            or carrier_bundle.authority_contract
            .no_auto_promotion
            is not True
        ):
            raise RCAFCanonicalLedgerError(
                "carrier pruning contract violates "
                "observe-only authority"
            )

        if (
            carrier_bundle.raw_content_stored
            is not False
            or carrier_bundle.content_fingerprint_stored
            is not False
        ):
            raise RCAFCanonicalLedgerError(
                "carrier coalition violates privacy"
            )

        organizational_future = (
            self.organizational_record.future_conditioning
        )

        if organizational_future is None:
            raise RCAFCanonicalLedgerError(
                "full framework record requires organizational "
                "future-conditioning evidence"
            )

        if (
            bundle.terminal_contract.contract_id
            != self.future_gate.future_contract_id
            or organizational_future.terminal_contract_id
            != self.future_gate.future_contract_id
        ):
            raise RCAFCanonicalLedgerError(
                "future contract mismatch across canonical records"
            )

        if (
            bundle.terminal_contract.terminal_class_id
            != self.future_gate.terminal_organizational_class_id
            or organizational_future.terminal_class_id
            != self.future_gate.terminal_organizational_class_id
        ):
            raise RCAFCanonicalLedgerError(
                "terminal organizational class mismatch"
            )

        if (
            bundle.forward_evidence.current_state_id
            != self.future_gate.current_state_id
        ):
            raise RCAFCanonicalLedgerError(
                "current-state linkage mismatch"
            )

        if (
            bundle.forward_evidence.reachable_set_id
            != self.future_gate.forward_reachable_set_id
        ):
            raise RCAFCanonicalLedgerError(
                "forward reachable-set linkage mismatch"
            )

        if (
            bundle.backward_evidence.predecessor_set_id
            != self.future_gate.backward_predecessor_set_id
        ):
            raise RCAFCanonicalLedgerError(
                "backward predecessor-set linkage mismatch"
            )

        if (
            self.future_gate.admissible_corridor_id
            not in bundle.consistency.shared_corridor_ids
            or organizational_future.corridor_id
            != self.future_gate.admissible_corridor_id
        ):
            raise RCAFCanonicalLedgerError(
                "admissible-corridor linkage mismatch"
            )

        if (
            bundle.forward_evidence.forward_reachable
            != self.future_gate.forward_reachable
            or bundle.backward_evidence.backward_consistent
            != self.future_gate.backward_consistent
            or bundle.consistency.gate_admissible
            != self.future_gate.gate_admissible
        ):
            raise RCAFCanonicalLedgerError(
                "future-gate conclusion mismatch"
            )

        if (
            bundle.backward_evidence.required_reference_contract_id
            != self.reference_conditioning.reference_contract_id
            or bundle.prospective_nomination.reference_contract_id
            != self.reference_conditioning.reference_contract_id
        ):
            raise RCAFCanonicalLedgerError(
                "future-authority evidence is conditioned on "
                "the wrong reference contract"
            )

        if (
            self.authority_lifecycle.future_corridor_id
            != self.future_gate.admissible_corridor_id
        ):
            raise RCAFCanonicalLedgerError(
                "authority lifecycle corridor mismatch"
            )

        if self.authority_posture == "observe_only":
            if (
                bundle.lifecycle.authority_status
                != "observe_only"
            ):
                raise RCAFCanonicalLedgerError(
                    "observe-only canonical record requires "
                    "observe-only future-gate authority status"
                )

            if bundle.causal_authority_eligible:
                raise RCAFCanonicalLedgerError(
                    "observe-only canonical record cannot be "
                    "future-authority eligible"
                )

        diagnostics = tuple(
            self.derived_diagnostics
        )

        if not diagnostics:
            raise RCAFCanonicalLedgerError(
                "derived_diagnostics must not be empty"
            )

        diagnostic_names = [
            item.diagnostic_name
            for item in diagnostics
        ]

        if len(set(diagnostic_names)) != len(diagnostic_names):
            raise RCAFCanonicalLedgerError(
                "derived_diagnostics contains duplicate names"
            )

        object.__setattr__(
            self,
            "derived_diagnostics",
            diagnostics,
        )

        for field_name in (
            "semantic_memory_authority",
            "identity_proof_established",
            "realization_authorized",
            "external_causal_authority",
            "self_modification_authority",
            "raw_content_stored",
            "content_fingerprint_stored",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_boolean(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if self.authority_posture == "observe_only":
            forbidden = {
                "semantic_memory_authority": (
                    self.semantic_memory_authority
                ),
                "identity_proof_established": (
                    self.identity_proof_established
                ),
                "realization_authorized": (
                    self.realization_authorized
                ),
                "external_causal_authority": (
                    self.external_causal_authority
                ),
                "self_modification_authority": (
                    self.self_modification_authority
                ),
            }

            active = sorted(
                name
                for name, value in forbidden.items()
                if value
            )

            if active:
                raise RCAFCanonicalLedgerError(
                    "observe_only posture forbids: "
                    f"{active!r}"
                )

        if self.raw_content_stored is not False:
            raise RCAFCanonicalLedgerError(
                "raw_content_stored must remain False"
            )

        if self.content_fingerprint_stored is not False:
            raise RCAFCanonicalLedgerError(
                "content_fingerprint_stored must remain False"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "schema": RCAF_CANONICAL_LEDGER_SCHEMA,
            "record_id": self.record_id,
            "event_spine_id": self.event_spine_id,
            "lineage_id": self.lineage_id,
            "occurred_at_utc": self.occurred_at_utc,
            "observer_lineage_id": self.observer_lineage_id,
            "observer_role_id": self.observer_role_id,
            "observer_effects_record_id": (
                self.observer_effects_record_id
            ),
            "organizational_record": (
                self.organizational_record.to_dict()
            ),
            "reference_conditioning": (
                self.reference_conditioning.to_dict()
            ),
            "primitives": self.primitives.to_dict(),
            "participation": self.participation.to_dict(),
            "process_chains": self.process_chains.to_dict(),
            "proposal_gain": self.proposal_gain.to_dict(),
            "transformation_geometry": (
                self.transformation_geometry.to_dict()
            ),
            "interface_propagation": (
                self.interface_propagation.to_dict()
            ),
            "future_gate": self.future_gate.to_dict(),
            "future_authority_evidence": (
                self.future_authority_evidence.to_dict()
            ),
            "carrier_coalition": (
                self.carrier_coalition.to_dict()
            ),
            "authority_lifecycle": (
                self.authority_lifecycle.to_dict()
            ),
            "geometric_references": (
                self.geometric_references.to_dict()
            ),
            "commitment": self.commitment.to_dict(),
            "event_coordinate": (
                self.event_coordinate.to_dict()
            ),
            "decision_trace": self.decision_trace.to_dict(),
            "derived_diagnostics": [
                item.to_dict()
                for item in self.derived_diagnostics
            ],
            "authority_posture": self.authority_posture,
            "semantic_memory_authority": False,
            "identity_proof_established": False,
            "realization_authorized": False,
            "external_causal_authority": False,
            "self_modification_authority": False,
            "raw_content_stored": False,
            "content_fingerprint_stored": False,
            "completeness": (
                canonical_completeness_report(
                    self
                )
            ),
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


def canonical_completeness_report(
    record: RCAFFullFrameworkRecord,
) -> dict:
    primitive_vectors = (
        record.primitives.coherence_psi,
        record.primitives.coherence_meta_field,
        record.primitives.activity_density_rho_a,
        record.primitives.coherence_density_rho_c,
        record.primitives.salience,
        record.primitives.realization_pressure_lambda,
        record.primitives.curiosity,
        record.primitives.viability_omega,
        record.primitives.occupancy_xi,
        record.primitives.future_freedom_f_xi,
        record.primitives.realization_coupling_kappa_r,
        record.primitives.mediation,
        record.primitives.feedback,
    )

    atlas_types = {
        link.atlas_type
        for link
        in record.organizational_record.atlas_links
    }

    future_bundle = record.future_authority_evidence
    carrier_bundle = record.carrier_coalition
    carrier = carrier_bundle.coalition

    carrier_relation_ids = {
        relation.relation_id
        for relation in carrier_bundle.validity_relations
    }

    carrier_role_evidence_ids = {
        evidence.evidence_id
        for evidence in carrier_bundle.role_evidence
    }

    carrier_turbulence_ids = {
        channel.channel_id
        for channel in carrier_bundle.turbulence_channels
    }

    carrier_counterfactual_arms = {
        branch.arm
        for branch in carrier_bundle.matched_experiment.branches
    }

    matched_roles = {
        branch.branch_role
        for branch
        in future_bundle.causal_experiment.branches
    }

    support_withdrawal_branches = [
        branch
        for branch
        in future_bundle.causal_experiment.branches
        if branch.branch_role
        == "A5_support_withdrawal"
    ]

    anti_coercion_conditions = (
        future_bundle.anti_coercion.prediction_preceded_intervention,
        future_bundle.anti_coercion.terminal_contract_frozen,
        future_bundle.anti_coercion.acceptance_criteria_frozen,
        future_bundle.anti_coercion.evaluator_independent,
        future_bundle.anti_coercion.target_rewrite_prevented,
        future_bundle.anti_coercion.evidence_rewrite_prevented,
        future_bundle.anti_coercion.control_branches_preserved,
        future_bundle.anti_coercion.valid_treatment_preferential,
        future_bundle.anti_coercion.support_withdrawal_persistent,
    )

    broad_stages = tuple(
        item.stage
        for item in record.process_chains.broad_chain
    )

    governance_stages = tuple(
        item.stage
        for item
        in record.process_chains.realization_governance_chain
    )

    interface_stages = tuple(
        item.stage
        for item
        in record.interface_propagation.observations
    )

    event_axes = tuple(
        record.event_coordinate.to_dict()
    )

    checks = {
        "canonical_primitives": (
            all(
                vector.components
                for vector in primitive_vectors
            )
            and bool(
                record.primitives.absorption_a_r_h
            )
        ),
        "broad_organizational_chain": (
            broad_stages
            == RCAF_BROAD_ORGANIZATIONAL_CHAIN
        ),
        "realization_governance_chain": (
            governance_stages
            == RCAF_REALIZATION_GOVERNANCE_CHAIN
        ),
        "reference_conditioning": (
            bool(
                record.reference_conditioning.observable_ids
            )
            and (
                record.primitives.reference_contract_id
                == record.reference_conditioning.reference_contract_id
            )
        ),
        "triadic_participation": math.isclose(
            record.participation.participation.pi_i
            + record.participation.participation.pi_r
            + record.participation.participation.pi_a,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-6,
        ),
        "participation_corridor": bool(
            record.participation.selected_structure_ids
            and record.participation.selected_direction_ids
            and record.participation.corridor_id
        ),
        "goldilocks_regulation": bool(
            record.participation.goldilocks_regime
            and record.participation.polarity_regime
        ),
        "harmonics_retention": bool(
            record.participation.harmonics_retention_state
        ),
        "proposal_gain_discovery": bool(
            record.proposal_gain.candidate_ids
        ),
        "verification_evidence": (
            record.organizational_record.evidence
            is not None
        ),
        "boundary_admissibility": (
            record.organizational_record.boundary_authority
            is not None
        ),
        "transformation_geometry": bool(
            record.transformation_geometry.geometry_components.components
        ),
        "multi_space_coordinates": all(
            bool(
                getattr(
                    record.transformation_geometry,
                    field_name,
                )
            )
            for field_name in (
                "event_state_space_id",
                "reference_space_id",
                "proposal_tangent_space_id",
                "causal_cone_space_id",
                "response_parameter_space_id",
                "outcome_composition_space_id",
                "invariant_quotient_space_id",
                "interface_coupling_space_id",
                "propagation_path_space_id",
                "operator_semigroup_space_id",
                "evidence_status_space_id",
                "meta_atlas_space_id",
            )
        ),
        "future_conditioned_authority": bool(
            record.future_gate.forward_reachable_set_id
            and record.future_gate.backward_predecessor_set_id
            and record.future_gate.admissible_corridor_id
        ),
        "terminal_organizational_class_contract": (
            bool(
                future_bundle.terminal_contract.contract_id
                and future_bundle.terminal_contract.terminal_class_id
            )
            and future_bundle.terminal_contract.frozen_before_treatment
            and future_bundle.terminal_contract.authority_non_expansion_required
            and future_bundle.terminal_contract.independent_verification_required
        ),
        "forward_transition_map": (
            bool(
                future_bundle.forward_evidence.forward_map_id
                and future_bundle.forward_evidence.reachable_set_id
                and future_bundle.forward_evidence.branches
                and future_bundle.forward_evidence.reachable_terminal_class_ids
            )
        ),
        "backward_predecessor_map": (
            bool(
                future_bundle.backward_evidence.backward_map_id
                and future_bundle.backward_evidence.predecessor_set_id
                and future_bundle.backward_evidence.required_condition_ids
                and future_bundle.backward_evidence.required_evaluator_ids
            )
        ),
        "bidirectional_corridor_consistency": (
            future_bundle.consistency.forward_reachable_set_id
            == future_bundle.forward_evidence.reachable_set_id
            and future_bundle.consistency.backward_predecessor_set_id
            == future_bundle.backward_evidence.predecessor_set_id
            and bool(
                future_bundle.consistency.shared_corridor_ids
            )
        ),
        "matched_causal_branching": (
            matched_roles == MATCHED_CAUSAL_BRANCH_ROLES
            and len(
                future_bundle.causal_experiment.branches
            )
            == len(MATCHED_CAUSAL_BRANCH_ROLES)
        ),
        "support_withdrawal_validation": (
            len(support_withdrawal_branches) == 1
            and support_withdrawal_branches[0].support_present
            is False
            and bool(
                future_bundle.causal_experiment
                .support_withdrawal_persistence.components
            )
        ),
        "anti_self_fulfilling_authority": (
            future_bundle.anti_coercion.passed
            == all(anti_coercion_conditions)
        ),
        "frozen_acceptance_criteria": (
            future_bundle.terminal_contract.frozen_before_treatment
            and bool(
                future_bundle.causal_experiment
                .frozen_acceptance_criteria_id
            )
        ),
        "independent_evaluator": (
            future_bundle.causal_experiment.independent_evaluator_id
            in future_bundle.backward_evidence.required_evaluator_ids
            and {
                branch.evaluator_id
                for branch
                in future_bundle.causal_experiment.branches
            }
            == {
                future_bundle.causal_experiment.independent_evaluator_id
            }
        ),
        "retrospective_atlas_extraction": (
            bool(
                future_bundle.retrospective_atlas.atlas_record_id
                and future_bundle.retrospective_atlas.precursor_class_id
            )
            and future_bundle.retrospective_atlas.authority_granted
            is False
        ),
        "prospective_gate_nomination": (
            bool(
                future_bundle.prospective_nomination.nomination_id
                and future_bundle.prospective_nomination.corridor_match_id
            )
            and future_bundle.prospective_nomination.authority_withheld
            is True
        ),
        "calibration_status": (
            future_bundle.calibration.status
            in CALIBRATION_STATES
        ),
        "scenario_tail_evidence": bool(
            future_bundle.calibration.scenario_tail_metrics.components
        ),
        "gate_maturity_lifecycle": (
            bool(
                future_bundle.lifecycle.lifecycle_id
                and future_bundle.lifecycle.calibration_id
                and future_bundle.lifecycle.causal_experiment_id
            )
            and future_bundle.lifecycle.no_auto_promotion
        ),
        "authority_evidence_separation": (
            future_bundle.lifecycle.no_auto_promotion
            and future_bundle.retrospective_atlas.authority_granted
            is False
            and future_bundle.prospective_nomination.authority_withheld
            is True
        ),
        "bidirectional_meta_field": (
            future_bundle.meta_field.forward_transition_map_id
            == future_bundle.forward_evidence.forward_map_id
            and future_bundle.meta_field.backward_predecessor_map_id
            == future_bundle.backward_evidence.backward_map_id
            and future_bundle.meta_field.consistency_relation_id
            == future_bundle.consistency.consistency_record_id
        ),
        "no_literal_retrocausality": (
            future_bundle.meta_field.literal_retrocausality_claimed
            is False
        ),
        "authority_lifecycle": bool(
            record.authority_lifecycle.proposal_id
            and record.authority_lifecycle.recipient_id
            and record.authority_lifecycle.operation_id
        ),
        "interface_propagation_distinctions": (
            interface_stages
            == RCAF_INTERFACE_PROPAGATION_CHAIN
        ),
        "realization_absorption_persistence_release": (
            {
                "realization_injection",
                "absorption",
                "influence",
                "persistence",
                "release",
            }
            .issubset(
                set(governance_stages)
            )
        ),
        "counterfactual_comparison": (
            record.organizational_record.counterfactual
            is not None
        ),
        "canonical_multi_atlas": (
            atlas_types == RCAF_ATLAS_TYPES
        ),
        "e8_reference_geometry": bool(
            record.geometric_references.e8.chart_id
        ),
        "leech_reference_geometry": bool(
            record.geometric_references.leech.atlas_id
        ),
        "monster_meta_atlas": bool(
            record.geometric_references.monster.meta_atlas_id
        ),
        "causal_cone_reference": bool(
            record.geometric_references.causal_cone.causal_cone_id
        ),
        "operator_flow_reference": bool(
            record.geometric_references.operator_flow.operator_id
        ),
        "minimal_event_coordinate": (
            event_axes == RCAF_EVENT_COORDINATE_AXES
        ),
        "commitment_hysteresis": bool(
            record.commitment.corridor_id
            and record.commitment.reference_contract_id
        ),
        "primitive_derived_separation": all(
            diagnostic.diagnostic_name
            not in RCAF_PRIMITIVE_NAMES
            and bool(
                diagnostic.derived_from_component_ids
            )
            for diagnostic
            in record.derived_diagnostics
        ),
        "layered_decision_trace": (
            record.decision_trace.order_preserved
            and bool(
                record.decision_trace.component_observable_ids
            )
            and bool(
                record.decision_trace.intermediate_geometry_ids
            )
            and bool(
                record.decision_trace.conditioned_decision_ids
            )
        ),
        "observer_effects_linkage": bool(
            record.observer_effects_record_id
        ),
        "observer_lineage_without_identity_claim": (
            bool(record.observer_lineage_id)
            and not record.identity_proof_established
        ),
        "privacy_boundary": (
            not record.raw_content_stored
            and not record.content_fingerprint_stored
        ),
        "trajectory_admissible_carrier_coalition": (
            bool(
                carrier.coalition_id
                and carrier.member_ids
                and carrier.initialization_state_id
                and carrier.optimizer_state_id
                and carrier.data_contract_id
                and carrier.reference_contract_id
                and carrier.participation_geometry_id
                and carrier.transformation_geometry_id
                and carrier.boundary_contract_id
                and carrier.terminal_organizational_class_id
            )
            and carrier.forward_reachable
            and carrier.backward_consistent
            and carrier.self_supporting
            and carrier.support_withdrawal_verified
            and carrier.observer_only
            and carrier.authority_eligible is False
            and carrier.no_auto_promotion
        ),
        "carrier_validity_relations": (
            bool(carrier_bundle.validity_relations)
            and all(
                relation.valid
                and relation.source_member_id
                in carrier.member_ids
                and relation.target_member_id
                in carrier.member_ids
                and relation.observer_only
                and relation.authority_granted is False
                for relation
                in carrier_bundle.validity_relations
            )
            and set(
                carrier.scaffold_relation_ids
            ).issubset(
                carrier_relation_ids
            )
        ),
        "multidimensional_component_roles": (
            bool(carrier_bundle.role_evidence)
            and all(
                tuple(evidence.role_vector)
                == COMPONENT_ROLE_NAMES
                and evidence.role_assignment_nonexclusive
                and evidence.member_id
                in carrier.member_ids
                for evidence
                in carrier_bundle.role_evidence
            )
            and set(
                carrier.role_evidence_ids
            ).issubset(
                carrier_role_evidence_ids
            )
        ),
        "scaffold_dependence": (
            bool(
                carrier_bundle.scaffold_dependence
                .scaffold_member_ids
            )
            and bool(
                carrier_bundle.scaffold_dependence
                .dependence_vector
            )
            and set(
                carrier_bundle.scaffold_dependence
                .scaffold_member_ids
            ).issubset(
                set(carrier.member_ids)
            )
        ),
        "scaffold_release_evidence": (
            bool(
                carrier_bundle.scaffold_release
                .scaffold_member_ids
            )
            and carrier_bundle.scaffold_release
            .release_admissible
            and carrier_bundle.scaffold_release
            .observer_only
            and carrier_bundle.scaffold_release
            .authority_granted
            is False
        ),
        "support_withdrawal_as_causal_intervention": (
            carrier.support_withdrawal_verified
            and carrier_bundle.scaffold_dependence
            .support_required_now
            is False
            and carrier_bundle.scaffold_release
            .withdrawal_horizon
            > 0
            and carrier_bundle.scaffold_release
            .persistence_verified
            and carrier_bundle.scaffold_release
            .recovery_available
        ),
        "multiscale_turbulence_channels": (
            bool(carrier_bundle.turbulence_channels)
            and all(
                tuple(channel.component_vector)
                == TURBULENCE_COMPONENT_NAMES
                and bool(channel.turbulence_classes)
                for channel
                in carrier_bundle.turbulence_channels
            )
            and set(
                carrier.turbulence_channel_ids
            ).issubset(
                carrier_turbulence_ids
            )
        ),
        "component_preserving_turbulence": (
            all(
                channel.component_preserving
                and channel.collapsed_score_authoritative
                is False
                for channel
                in carrier_bundle.turbulence_channels
            )
        ),
        "turbulent_debt_observation": (
            all(
                "turbulent_debt"
                in channel.component_vector
                for channel
                in carrier_bundle.turbulence_channels
            )
            and carrier_bundle.scaffold_release
            .turbulent_debt_admissible
        ),
        "compression_future_freedom": (
            tuple(
                carrier_bundle.future_freedom
                .retention_by_component
            )
            == FUTURE_FREEDOM_COMPONENT_NAMES
            and carrier_bundle.future_freedom.preserved
            and carrier_bundle.scaffold_release
            .future_freedom_preserved
        ),
        "topology_initialization_context_counterfactuals": (
            carrier_counterfactual_arms
            == set(COUNTERFACTUAL_ARMS)
            and all(
                branch.topology_relation
                and branch.initialization_relation
                and branch.context_relation
                and branch.support_relation
                for branch
                in carrier_bundle.matched_experiment.branches
            )
        ),
        "dynamic_topology_counterfactual": (
            len(
                [
                    branch
                    for branch
                    in carrier_bundle.matched_experiment.branches
                    if branch.arm == "A8"
                    and branch.dynamic_topology
                ]
            )
            == 1
        ),
        "microscopic_ticket_equivalence": (
            len(
                carrier_bundle.equivalence_class.coalition_ids
            )
            >= 2
            and carrier_bundle.equivalence_class
            .equivalent_terminal_organization
            and carrier_bundle.equivalence_class
            .identical_microstate_required
            is False
            and carrier.coalition_id
            in carrier_bundle.equivalence_class.coalition_ids
        ),
        "prospective_coalition_nomination": (
            carrier_bundle.nomination.frozen_criteria
            and carrier_bundle.nomination.independent_evaluator
            and carrier_bundle.nomination.reversible
            and carrier_bundle.nomination.observer_only
            and carrier_bundle.nomination.authority_withheld
            and carrier_bundle.nomination.matched_experiment_id
            == carrier_bundle.matched_experiment.experiment_id
        ),
        "reversible_pruning_contract": (
            carrier_bundle.authority_contract.authority_status
            == "observe_only"
            and carrier_bundle.authority_contract
            .external_causal_authority
            is False
            and carrier_bundle.authority_contract
            .self_modification_authority
            is False
            and carrier_bundle.authority_contract
            .no_auto_promotion
            and carrier_bundle.authority_contract
            .rollback_checkpoint_id
            and carrier_bundle.authority_contract
            .release_criteria_ids
        ),
    }

    satisfied = sorted(
        key
        for key, value in checks.items()
        if value
    )

    missing = sorted(
        RCAF_CANONICAL_CAPABILITIES
        - set(satisfied)
    )

    return {
        "required_capability_count": len(
            RCAF_CANONICAL_CAPABILITIES
        ),
        "satisfied_capability_count": len(
            satisfied
        ),
        "satisfied_capabilities": satisfied,
        "missing_capabilities": missing,
        "complete": not missing,
    }
