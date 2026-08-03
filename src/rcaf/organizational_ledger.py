# ============================================================
# src/rcaf/organizational_ledger.py
# ============================================================

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


RCAF_ORGANIZATIONAL_LEDGER_SCHEMA = (
    "RCAF-ORGANIZATIONAL-LEDGER-0.1"
)

RCAF_ATLAS_TYPES = frozenset(
    {
        "State",
        "Response",
        "Transition",
        "Reference",
        "Boundary",
        "Authority",
        "TemporalAnchor",
        "Interface",
        "Coupling",
        "PropagationInfluence",
        "ScaleTransition",
        "GlobalReferenceBackreaction",
        "Persistence",
        "Evidence",
        "CausalFlow",
        "FutureTopology",
        "SymmetryMetaAtlas",
    }
)

RCAF_RECORD_TYPES = frozenset(
    {
        "event_spine",
        "state_transition",
        "evidence",
        "boundary",
        "authority",
        "realization_lifecycle",
        "counterfactual",
        "atlas_linkage",
        "composite",
    }
)

RCAF_STAGES = frozenset(
    {
        "possibility",
        "proposal",
        "verification",
        "transition_qualification",
        "edge_admissibility",
        "geometry_coherence",
        "transformation_geometry",
        "active_participation_permission",
        "realization",
        "absorption",
        "influence",
        "persistence",
        "release",
        "modified_possibility",
    }
)

EVIDENCE_STATUSES = frozenset(
    {
        "unknown",
        "unverified",
        "partial",
        "verified",
        "contradictory",
        "insufficient",
        "rejected",
    }
)

BOUNDARY_STATES = frozenset(
    {
        "unknown",
        "open",
        "bounded",
        "closed",
        "violated",
    }
)

ADMISSIBILITY_STATES = frozenset(
    {
        "unknown",
        "pending",
        "admissible",
        "inadmissible",
        "deferred",
    }
)

AUTHORITY_STATES = frozenset(
    {
        "none",
        "proposed",
        "bounded_grant",
        "active",
        "denied",
        "expired",
        "released",
        "revoked",
    }
)

LIFECYCLE_STATUSES = frozenset(
    {
        "not_observed",
        "possible",
        "proposed",
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
        "failed",
        "deferred",
        "rejected",
    }
)

COUNTERFACTUAL_STATUSES = frozenset(
    {
        "not_evaluated",
        "partial",
        "complete",
        "inconclusive",
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

_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$"
)


class RCAFOrganizationalLedgerError(ValueError):
    """Raised when a multidimensional RCAF record is invalid."""


def _require_identifier(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise RCAFOrganizationalLedgerError(
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
        raise RCAFOrganizationalLedgerError(
            f"{field_name} must be one of {sorted(allowed)!r}; "
            f"received {normalized!r}"
        )

    return normalized


def _require_boolean(
    field_name: str,
    value: bool,
) -> bool:
    if not isinstance(value, bool):
        raise RCAFOrganizationalLedgerError(
            f"{field_name} must be bool"
        )

    return value


def _require_finite(
    field_name: str,
    value: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise RCAFOrganizationalLedgerError(
            f"{field_name} must be a finite number"
        )

    return float(value)


def _optional_finite(
    field_name: str,
    value: float | None,
) -> float | None:
    if value is None:
        return None

    return _require_finite(
        field_name,
        value,
    )


def _require_unit_interval(
    field_name: str,
    value: float,
) -> float:
    normalized = _require_finite(
        field_name,
        value,
    )

    if not 0.0 <= normalized <= 1.0:
        raise RCAFOrganizationalLedgerError(
            f"{field_name} must be between 0 and 1"
        )

    return normalized


def _require_nonnegative_integer(
    field_name: str,
    value: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise RCAFOrganizationalLedgerError(
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
        raise RCAFOrganizationalLedgerError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc

    if (
        parsed.tzinfo is None
        or parsed.utcoffset()
        != timezone.utc.utcoffset(parsed)
    ):
        raise RCAFOrganizationalLedgerError(
            f"{field_name} must be UTC"
        )

    return normalized


def _normalize_identifiers(
    field_name: str,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    normalized = tuple(
        _require_identifier(
            field_name,
            value,
        )
        for value in values
    )

    if len(set(normalized)) != len(normalized):
        raise RCAFOrganizationalLedgerError(
            f"{field_name} contains duplicate identifiers"
        )

    return normalized


@dataclass(frozen=True)
class MetricVector:
    components: tuple[
        tuple[str, float],
        ...,
    ] = ()

    def __post_init__(
        self,
    ) -> None:
        normalized = []
        seen = set()

        for name, value in self.components:
            component_name = _require_identifier(
                "metric component",
                name,
            )

            if component_name in seen:
                raise RCAFOrganizationalLedgerError(
                    "metric vector contains duplicate components"
                )

            seen.add(component_name)

            normalized.append(
                (
                    component_name,
                    _require_finite(
                        component_name,
                        value,
                    ),
                )
            )

        object.__setattr__(
            self,
            "components",
            tuple(sorted(normalized)),
        )

    def to_dict(
        self,
    ) -> dict[str, float]:
        return {
            name: value
            for name, value in self.components
        }


@dataclass(frozen=True)
class AtlasLink:
    atlas_type: str
    atlas_record_id: str

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "atlas_type",
            _require_choice(
                "atlas_type",
                self.atlas_type,
                RCAF_ATLAS_TYPES,
            ),
        )

        object.__setattr__(
            self,
            "atlas_record_id",
            _require_identifier(
                "atlas_record_id",
                self.atlas_record_id,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "atlas_type": self.atlas_type,
            "atlas_record_id": self.atlas_record_id,
        }


@dataclass(frozen=True)
class ReferenceGeometry:
    reference_id: str
    reference_class_id: str
    anchor_id: str | None = None
    drift: MetricVector = field(
        default_factory=MetricVector
    )
    deviation: MetricVector = field(
        default_factory=MetricVector
    )
    future_freedom: MetricVector = field(
        default_factory=MetricVector
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "reference_id",
            _require_identifier(
                "reference_id",
                self.reference_id,
            ),
        )

        object.__setattr__(
            self,
            "reference_class_id",
            _require_identifier(
                "reference_class_id",
                self.reference_class_id,
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

    def to_dict(
        self,
    ) -> dict:
        return {
            "reference_id": self.reference_id,
            "reference_class_id": self.reference_class_id,
            "anchor_id": self.anchor_id,
            "drift": self.drift.to_dict(),
            "deviation": self.deviation.to_dict(),
            "future_freedom": self.future_freedom.to_dict(),
        }


@dataclass(frozen=True)
class ParticipationGeometry:
    pi_i: float
    pi_r: float
    pi_a: float
    strain: float

    def __post_init__(
        self,
    ) -> None:
        values = {
            "pi_i": _require_unit_interval(
                "pi_i",
                self.pi_i,
            ),
            "pi_r": _require_unit_interval(
                "pi_r",
                self.pi_r,
            ),
            "pi_a": _require_unit_interval(
                "pi_a",
                self.pi_a,
            ),
            "strain": _require_unit_interval(
                "strain",
                self.strain,
            ),
        }

        total = (
            values["pi_i"]
            + values["pi_r"]
            + values["pi_a"]
        )

        if not math.isclose(
            total,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise RCAFOrganizationalLedgerError(
                "pi_i + pi_r + pi_a must equal 1"
            )

        for name, value in values.items():
            object.__setattr__(
                self,
                name,
                value,
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "pi_i": self.pi_i,
            "pi_r": self.pi_r,
            "pi_a": self.pi_a,
            "strain": self.strain,
        }


@dataclass(frozen=True)
class CoherenceGeometry:
    meta_field_id: str | None = None
    psi: float | None = None
    rho_a: float | None = None
    rho_c: float | None = None
    occupancy: float | None = None
    geometry_coherence: float | None = None
    realization_pressure: float | None = None
    viability: float | None = None
    realization_coupling: float | None = None

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "meta_field_id",
            _optional_identifier(
                "meta_field_id",
                self.meta_field_id,
            ),
        )

        for field_name in (
            "psi",
            "rho_a",
            "rho_c",
            "occupancy",
            "geometry_coherence",
            "realization_pressure",
            "viability",
            "realization_coupling",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_finite(
                    field_name,
                    getattr(self, field_name),
                ),
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "meta_field_id": self.meta_field_id,
            "psi": self.psi,
            "rho_a": self.rho_a,
            "rho_c": self.rho_c,
            "occupancy": self.occupancy,
            "geometry_coherence": self.geometry_coherence,
            "realization_pressure": self.realization_pressure,
            "viability": self.viability,
            "realization_coupling": self.realization_coupling,
        }


@dataclass(frozen=True)
class EvidenceGeometry:
    status: str
    evidence_ids: tuple[str, ...] = ()
    benefit: MetricVector = field(
        default_factory=MetricVector
    )
    harm: MetricVector = field(
        default_factory=MetricVector
    )
    containment: MetricVector = field(
        default_factory=MetricVector
    )
    reversibility: MetricVector = field(
        default_factory=MetricVector
    )
    confidence: MetricVector = field(
        default_factory=MetricVector
    )
    future_freedom_delta: MetricVector = field(
        default_factory=MetricVector
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "status",
            _require_choice(
                "evidence status",
                self.status,
                EVIDENCE_STATUSES,
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
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "benefit": self.benefit.to_dict(),
            "harm": self.harm.to_dict(),
            "containment": self.containment.to_dict(),
            "reversibility": self.reversibility.to_dict(),
            "confidence": self.confidence.to_dict(),
            "future_freedom_delta": (
                self.future_freedom_delta.to_dict()
            ),
        }


@dataclass(frozen=True)
class FutureConditioning:
    horizon: int
    terminal_contract_id: str
    terminal_class_id: str
    corridor_id: str
    forward_reachable: bool
    backward_consistent: bool
    gate_admissible: bool

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

        for field_name in (
            "terminal_contract_id",
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

    def to_dict(
        self,
    ) -> dict:
        return {
            "horizon": self.horizon,
            "terminal_contract_id": self.terminal_contract_id,
            "terminal_class_id": self.terminal_class_id,
            "corridor_id": self.corridor_id,
            "forward_reachable": self.forward_reachable,
            "backward_consistent": self.backward_consistent,
            "gate_admissible": self.gate_admissible,
        }


@dataclass(frozen=True)
class BoundaryAuthority:
    boundary_state: str
    admissibility_state: str
    authority_state: str
    authority_id: str | None = None
    scope_ids: tuple[str, ...] = ()
    release_condition_ids: tuple[str, ...] = ()
    revocation_condition_ids: tuple[str, ...] = ()
    realization_authorized: bool = False

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "boundary_state",
            _require_choice(
                "boundary_state",
                self.boundary_state,
                BOUNDARY_STATES,
            ),
        )

        object.__setattr__(
            self,
            "admissibility_state",
            _require_choice(
                "admissibility_state",
                self.admissibility_state,
                ADMISSIBILITY_STATES,
            ),
        )

        object.__setattr__(
            self,
            "authority_state",
            _require_choice(
                "authority_state",
                self.authority_state,
                AUTHORITY_STATES,
            ),
        )

        object.__setattr__(
            self,
            "authority_id",
            _optional_identifier(
                "authority_id",
                self.authority_id,
            ),
        )

        for field_name in (
            "scope_ids",
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
            "realization_authorized",
            _require_boolean(
                "realization_authorized",
                self.realization_authorized,
            ),
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "boundary_state": self.boundary_state,
            "admissibility_state": self.admissibility_state,
            "authority_state": self.authority_state,
            "authority_id": self.authority_id,
            "scope_ids": list(self.scope_ids),
            "release_condition_ids": list(
                self.release_condition_ids
            ),
            "revocation_condition_ids": list(
                self.revocation_condition_ids
            ),
            "realization_authorized": (
                self.realization_authorized
            ),
        }


@dataclass(frozen=True)
class RealizationLifecycle:
    possibility: str = "not_observed"
    proposal: str = "not_observed"
    verification: str = "not_observed"
    transition_qualification: str = "not_observed"
    edge_admissibility: str = "not_observed"
    geometry_coherence: str = "not_observed"
    active_participation_permission: str = "not_observed"
    realization: str = "not_observed"
    absorption: str = "not_observed"
    influence: str = "not_observed"
    persistence: str = "not_observed"
    release: str = "not_observed"

    def __post_init__(
        self,
    ) -> None:
        for field_name in self.__dataclass_fields__:
            object.__setattr__(
                self,
                field_name,
                _require_choice(
                    field_name,
                    getattr(self, field_name),
                    LIFECYCLE_STATUSES,
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
            for field_name
            in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class CausalFlow:
    interface_id: str | None = None
    coupling_id: str | None = None
    propagation_path_id: str | None = None
    scale_transition_id: str | None = None
    global_influence_id: str | None = None
    coupling_strength: MetricVector = field(
        default_factory=MetricVector
    )
    propagation_strength: MetricVector = field(
        default_factory=MetricVector
    )

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "interface_id",
            "coupling_id",
            "propagation_path_id",
            "scale_transition_id",
            "global_influence_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "interface_id": self.interface_id,
            "coupling_id": self.coupling_id,
            "propagation_path_id": (
                self.propagation_path_id
            ),
            "scale_transition_id": (
                self.scale_transition_id
            ),
            "global_influence_id": (
                self.global_influence_id
            ),
            "coupling_strength": (
                self.coupling_strength.to_dict()
            ),
            "propagation_strength": (
                self.propagation_strength.to_dict()
            ),
        }


@dataclass(frozen=True)
class CounterfactualComparison:
    status: str
    candidate_ids: tuple[str, ...]
    selected_candidate_id: str | None = None
    comparison_metrics: MetricVector = field(
        default_factory=MetricVector
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "status",
            _require_choice(
                "counterfactual status",
                self.status,
                COUNTERFACTUAL_STATUSES,
            ),
        )

        candidates = _normalize_identifiers(
            "candidate_ids",
            self.candidate_ids,
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
            raise RCAFOrganizationalLedgerError(
                "selected_candidate_id must occur in candidate_ids"
            )

        object.__setattr__(
            self,
            "selected_candidate_id",
            selected,
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "status": self.status,
            "candidate_ids": list(self.candidate_ids),
            "selected_candidate_id": self.selected_candidate_id,
            "comparison_metrics": (
                self.comparison_metrics.to_dict()
            ),
        }


@dataclass(frozen=True)
class RCAFOrganizationalRecord:
    record_id: str
    event_id: str
    lineage_id: str
    occurred_at_utc: str
    record_type: str
    stage: str
    atlas_links: tuple[AtlasLink, ...]
    reference: ReferenceGeometry | None = None
    participation: ParticipationGeometry | None = None
    coherence_geometry: CoherenceGeometry | None = None
    evidence: EvidenceGeometry | None = None
    future_conditioning: FutureConditioning | None = None
    boundary_authority: BoundaryAuthority | None = None
    lifecycle: RealizationLifecycle | None = None
    causal_flow: CausalFlow | None = None
    counterfactual: CounterfactualComparison | None = None
    authority_posture: str = "observe_only"
    raw_content_stored: bool = False
    content_fingerprint_stored: bool = False

    def __post_init__(
        self,
    ) -> None:
        for field_name in (
            "record_id",
            "event_id",
            "lineage_id",
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
            "record_type",
            _require_choice(
                "record_type",
                self.record_type,
                RCAF_RECORD_TYPES,
            ),
        )

        object.__setattr__(
            self,
            "stage",
            _require_choice(
                "stage",
                self.stage,
                RCAF_STAGES,
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

        atlas_types = [
            link.atlas_type
            for link in self.atlas_links
        ]

        if not self.atlas_links:
            raise RCAFOrganizationalLedgerError(
                "atlas_links must not be empty"
            )

        if len(set(atlas_types)) != len(atlas_types):
            raise RCAFOrganizationalLedgerError(
                "atlas_links contains duplicate atlas types"
            )

        if self.raw_content_stored is not False:
            raise RCAFOrganizationalLedgerError(
                "raw_content_stored must remain False"
            )

        if self.content_fingerprint_stored is not False:
            raise RCAFOrganizationalLedgerError(
                "content_fingerprint_stored must remain False"
            )

    def structural_summary(
        self,
    ) -> dict:
        sections = {
            "reference": self.reference is not None,
            "participation": self.participation is not None,
            "coherence_geometry": (
                self.coherence_geometry is not None
            ),
            "evidence": self.evidence is not None,
            "future_conditioning": (
                self.future_conditioning is not None
            ),
            "boundary_authority": (
                self.boundary_authority is not None
            ),
            "lifecycle": self.lifecycle is not None,
            "causal_flow": self.causal_flow is not None,
            "counterfactual": (
                self.counterfactual is not None
            ),
        }

        return {
            "atlas_count": len(self.atlas_links),
            "atlas_types": sorted(
                link.atlas_type
                for link in self.atlas_links
            ),
            "section_count": sum(
                sections.values()
            ),
            "sections": sections,
            "authority_posture": self.authority_posture,
        }

    def to_dict(
        self,
    ) -> dict:
        return {
            "schema": RCAF_ORGANIZATIONAL_LEDGER_SCHEMA,
            "record_id": self.record_id,
            "event_id": self.event_id,
            "lineage_id": self.lineage_id,
            "occurred_at_utc": self.occurred_at_utc,
            "record_type": self.record_type,
            "stage": self.stage,
            "atlas_links": [
                link.to_dict()
                for link in self.atlas_links
            ],
            "reference": (
                self.reference.to_dict()
                if self.reference is not None
                else None
            ),
            "participation": (
                self.participation.to_dict()
                if self.participation is not None
                else None
            ),
            "coherence_geometry": (
                self.coherence_geometry.to_dict()
                if self.coherence_geometry is not None
                else None
            ),
            "evidence": (
                self.evidence.to_dict()
                if self.evidence is not None
                else None
            ),
            "future_conditioning": (
                self.future_conditioning.to_dict()
                if self.future_conditioning is not None
                else None
            ),
            "boundary_authority": (
                self.boundary_authority.to_dict()
                if self.boundary_authority is not None
                else None
            ),
            "lifecycle": (
                self.lifecycle.to_dict()
                if self.lifecycle is not None
                else None
            ),
            "causal_flow": (
                self.causal_flow.to_dict()
                if self.causal_flow is not None
                else None
            ),
            "counterfactual": (
                self.counterfactual.to_dict()
                if self.counterfactual is not None
                else None
            ),
            "authority_posture": self.authority_posture,
            "raw_content_stored": False,
            "content_fingerprint_stored": False,
            "summary": self.structural_summary(),
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
