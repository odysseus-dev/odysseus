# ============================================================
# src/rcaf/canonical_evolution.py
# ============================================================

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.rcaf.canonical_ledger import (
    RCAF_CANONICAL_LEDGER_SCHEMA,
)

from src.rcaf.carrier_coalition import (
    RCAF_CARRIER_COALITION_SCHEMA,
)

from src.rcaf.future_authority_ledger import (
    RCAF_FUTURE_AUTHORITY_SCHEMA,
)

from src.rcaf.organizational_ledger import (
    RCAF_ORGANIZATIONAL_LEDGER_SCHEMA,
)


RCAF_SCHEMA_EVOLUTION_SCHEMA = (
    "RCAF-SCHEMA-EVOLUTION-0.2"
)

SCHEMA_DISPOSITIONS = frozenset(
    {
        "current",
        "migration_required",
        "unsupported",
    }
)

CURRENT_SCHEMA_BY_FAMILY = {
    "canonical_record": RCAF_CANONICAL_LEDGER_SCHEMA,
    "carrier_coalition": RCAF_CARRIER_COALITION_SCHEMA,
    "future_authority": RCAF_FUTURE_AUTHORITY_SCHEMA,
    "organizational_record": RCAF_ORGANIZATIONAL_LEDGER_SCHEMA,
}

KNOWN_PREVIOUS_SCHEMAS_BY_FAMILY = {
    "canonical_record": frozenset(
        {
            "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.2",
            "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.3",
        }
    ),
    "carrier_coalition": frozenset(),
    "future_authority": frozenset(),
    "organizational_record": frozenset(),
}

RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS = (
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


_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$"
)

_FIELD_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\[\]-]{0,255}$"
)

_SHA256_PATTERN = re.compile(
    r"^[0-9a-f]{64}$"
)


class CanonicalSchemaEvolutionError(ValueError):
    """Raised when a canonical schema-evolution contract is invalid."""


def _require_identifier(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise CanonicalSchemaEvolutionError(
            f"{field_name} must be a static structural identifier"
        )

    return normalized


def _require_schema_family(
    value: str,
) -> str:
    normalized = str(value).strip()

    if normalized not in CURRENT_SCHEMA_BY_FAMILY:
        raise CanonicalSchemaEvolutionError(
            "unknown schema family"
        )

    return normalized


def _require_schema_name(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if (
        not normalized
        or len(normalized) > 255
        or any(
            character.isspace()
            for character in normalized
        )
    ):
        raise CanonicalSchemaEvolutionError(
            f"{field_name} must be a schema identifier"
        )

    return normalized


def _require_sha256(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _SHA256_PATTERN.fullmatch(normalized):
        raise CanonicalSchemaEvolutionError(
            f"{field_name} must be lowercase SHA-256"
        )

    return normalized


def _require_field_path(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _FIELD_PATH_PATTERN.fullmatch(normalized):
        raise CanonicalSchemaEvolutionError(
            f"{field_name} contains an invalid field path"
        )

    return normalized


def _normalize_field_paths(
    field_name: str,
    values: tuple[str, ...],
    *,
    require_nonempty: bool = False,
) -> tuple[str, ...]:
    normalized = tuple(
        _require_field_path(
            field_name,
            value,
        )
        for value in values
    )

    if require_nonempty and not normalized:
        raise CanonicalSchemaEvolutionError(
            f"{field_name} must not be empty"
        )

    if len(set(normalized)) != len(normalized):
        raise CanonicalSchemaEvolutionError(
            f"{field_name} contains duplicate field paths"
        )

    return normalized


@dataclass(frozen=True)
class SchemaEvaluation:
    family: str
    observed_schema: str
    current_schema: str
    disposition: str
    automatic_migration_allowed: bool = False

    def __post_init__(
        self,
    ) -> None:
        family = _require_schema_family(
            self.family
        )

        object.__setattr__(
            self,
            "family",
            family,
        )

        object.__setattr__(
            self,
            "observed_schema",
            _require_schema_name(
                "observed_schema",
                self.observed_schema,
            ),
        )

        expected = CURRENT_SCHEMA_BY_FAMILY[
            family
        ]

        if self.current_schema != expected:
            raise CanonicalSchemaEvolutionError(
                "current_schema does not match the registry"
            )

        if self.disposition not in SCHEMA_DISPOSITIONS:
            raise CanonicalSchemaEvolutionError(
                "invalid schema disposition"
            )

        if self.automatic_migration_allowed is not False:
            raise CanonicalSchemaEvolutionError(
                "automatic schema migration is forbidden"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "family": self.family,
            "observed_schema": self.observed_schema,
            "current_schema": self.current_schema,
            "disposition": self.disposition,
            "automatic_migration_allowed": False,
        }


@dataclass(frozen=True)
class CanonicalSchemaSetValidation:
    canonical_record: SchemaEvaluation
    carrier_coalition: SchemaEvaluation
    organizational_record: SchemaEvaluation
    future_authority: SchemaEvaluation

    def __post_init__(
        self,
    ) -> None:
        expected_families = (
            ("canonical_record", "canonical_record"),
            ("carrier_coalition", "carrier_coalition"),
            ("organizational_record", "organizational_record"),
            ("future_authority", "future_authority"),
        )

        for field_name, expected_family in expected_families:
            evaluation = getattr(
                self,
                field_name,
            )

            if not isinstance(
                evaluation,
                SchemaEvaluation,
            ):
                raise CanonicalSchemaEvolutionError(
                    f"{field_name} must be SchemaEvaluation"
                )

            if evaluation.family != expected_family:
                raise CanonicalSchemaEvolutionError(
                    f"{field_name} must evaluate {expected_family}"
                )

    @property
    def current(
        self,
    ) -> bool:
        return all(
            evaluation.disposition
            == "current"
            for evaluation in (
                self.canonical_record,
                self.carrier_coalition,
                self.organizational_record,
                self.future_authority,
            )
        )

    def to_dict(
        self,
    ) -> dict:
        return {
            "schema": RCAF_SCHEMA_EVOLUTION_SCHEMA,
            "canonical_record": (
                self.canonical_record.to_dict()
            ),
            "carrier_coalition": (
                self.carrier_coalition.to_dict()
            ),
            "organizational_record": (
                self.organizational_record.to_dict()
            ),
            "future_authority": (
                self.future_authority.to_dict()
            ),
            "current": self.current,
            "automatic_migration_allowed": False,
        }


@dataclass(frozen=True)
class LosslessMigrationManifest:
    migration_id: str
    family: str
    source_schema: str
    target_schema: str
    source_record_sha256: str
    target_record_sha256: str
    preserved_field_paths: tuple[str, ...]
    renamed_field_paths: tuple[
        tuple[str, str],
        ...,
    ] = ()
    added_explicit_field_paths: tuple[str, ...] = ()
    dropped_field_paths: tuple[str, ...] = ()
    inferred_field_paths: tuple[str, ...] = ()
    authority_posture_before: str = "observe_only"
    authority_posture_after: str = "observe_only"
    lossless: bool = True
    automatic_migration: bool = False
    raw_content_stored: bool = False
    content_fingerprint_stored: bool = False

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "migration_id",
            _require_identifier(
                "migration_id",
                self.migration_id,
            ),
        )

        family = _require_schema_family(
            self.family
        )

        object.__setattr__(
            self,
            "family",
            family,
        )

        source_schema = _require_schema_name(
            "source_schema",
            self.source_schema,
        )

        target_schema = _require_schema_name(
            "target_schema",
            self.target_schema,
        )

        if (
            source_schema
            not in KNOWN_PREVIOUS_SCHEMAS_BY_FAMILY[
                family
            ]
        ):
            raise CanonicalSchemaEvolutionError(
                "source schema is not a registered previous schema"
            )

        if (
            target_schema
            != CURRENT_SCHEMA_BY_FAMILY[
                family
            ]
        ):
            raise CanonicalSchemaEvolutionError(
                "target schema must be the current registered schema"
            )

        object.__setattr__(
            self,
            "source_schema",
            source_schema,
        )

        object.__setattr__(
            self,
            "target_schema",
            target_schema,
        )

        for field_name in (
            "source_record_sha256",
            "target_record_sha256",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_sha256(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        object.__setattr__(
            self,
            "preserved_field_paths",
            _normalize_field_paths(
                "preserved_field_paths",
                self.preserved_field_paths,
                require_nonempty=True,
            ),
        )

        renamed = []

        for source_path, target_path in (
            self.renamed_field_paths
        ):
            renamed.append(
                (
                    _require_field_path(
                        "renamed source path",
                        source_path,
                    ),
                    _require_field_path(
                        "renamed target path",
                        target_path,
                    ),
                )
            )

        if len(set(renamed)) != len(renamed):
            raise CanonicalSchemaEvolutionError(
                "renamed_field_paths contains duplicates"
            )

        object.__setattr__(
            self,
            "renamed_field_paths",
            tuple(renamed),
        )

        for field_name in (
            "added_explicit_field_paths",
            "dropped_field_paths",
            "inferred_field_paths",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_field_paths(
                    field_name,
                    getattr(self, field_name),
                ),
            )

        if (
            self.family == "canonical_record"
            and self.source_schema
            == "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.3"
        ):
            missing_explicit_paths = tuple(
                path
                for path in RCAF_CANONICAL_0_3_TO_0_4_REQUIRED_EXPLICIT_PATHS
                if path not in self.added_explicit_field_paths
            )

            if missing_explicit_paths:
                raise CanonicalSchemaEvolutionError(
                    "canonical 0.3 to 0.4 migration requires "
                    "all explicit carrier evidence paths"
                )

        if self.dropped_field_paths:
            raise CanonicalSchemaEvolutionError(
                "lossless migration cannot drop fields"
            )

        if self.inferred_field_paths:
            raise CanonicalSchemaEvolutionError(
                "migration cannot infer missing RCAF fields"
            )

        if (
            self.authority_posture_before
            != self.authority_posture_after
        ):
            raise CanonicalSchemaEvolutionError(
                "migration cannot change authority posture"
            )

        if self.lossless is not True:
            raise CanonicalSchemaEvolutionError(
                "migration must be explicitly lossless"
            )

        if self.automatic_migration is not False:
            raise CanonicalSchemaEvolutionError(
                "automatic migration is forbidden"
            )

        if self.raw_content_stored is not False:
            raise CanonicalSchemaEvolutionError(
                "raw_content_stored must remain False"
            )

        if (
            self.content_fingerprint_stored
            is not False
        ):
            raise CanonicalSchemaEvolutionError(
                "content_fingerprint_stored must remain False"
            )

    def to_dict(
        self,
    ) -> dict:
        return {
            "schema": RCAF_SCHEMA_EVOLUTION_SCHEMA,
            "migration_id": self.migration_id,
            "family": self.family,
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "source_record_sha256": self.source_record_sha256,
            "target_record_sha256": self.target_record_sha256,
            "preserved_field_paths": list(
                self.preserved_field_paths
            ),
            "renamed_field_paths": [
                {
                    "source": source,
                    "target": target,
                }
                for source, target
                in self.renamed_field_paths
            ],
            "added_explicit_field_paths": list(
                self.added_explicit_field_paths
            ),
            "dropped_field_paths": [],
            "inferred_field_paths": [],
            "authority_posture_before": (
                self.authority_posture_before
            ),
            "authority_posture_after": (
                self.authority_posture_after
            ),
            "lossless": True,
            "automatic_migration": False,
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


def evaluate_schema(
    family: str,
    observed_schema: str,
) -> SchemaEvaluation:
    family = _require_schema_family(
        family
    )

    observed_schema = _require_schema_name(
        "observed_schema",
        observed_schema,
    )

    current_schema = (
        CURRENT_SCHEMA_BY_FAMILY[
            family
        ]
    )

    if observed_schema == current_schema:
        disposition = "current"

    elif (
        observed_schema
        in KNOWN_PREVIOUS_SCHEMAS_BY_FAMILY[
            family
        ]
    ):
        disposition = "migration_required"

    else:
        disposition = "unsupported"

    return SchemaEvaluation(
        family=family,
        observed_schema=observed_schema,
        current_schema=current_schema,
        disposition=disposition,
    )


def require_current_schema(
    family: str,
    observed_schema: str,
) -> SchemaEvaluation:
    evaluation = evaluate_schema(
        family,
        observed_schema,
    )

    if evaluation.disposition == "migration_required":
        raise CanonicalSchemaEvolutionError(
            f"{family} requires an explicit lossless migration manifest"
        )

    if evaluation.disposition == "unsupported":
        raise CanonicalSchemaEvolutionError(
            f"{family} schema is unsupported"
        )

    return evaluation


def validate_canonical_record_schema_set(
    record: dict[str, Any],
) -> CanonicalSchemaSetValidation:
    if not isinstance(record, dict):
        raise CanonicalSchemaEvolutionError(
            "canonical record must be an object"
        )

    carrier_coalition = record.get(
        "carrier_coalition"
    )

    organizational = record.get(
        "organizational_record"
    )

    future_authority = record.get(
        "future_authority_evidence"
    )

    if not isinstance(carrier_coalition, dict):
        raise CanonicalSchemaEvolutionError(
            "carrier-coalition record is missing"
        )

    if not isinstance(organizational, dict):
        raise CanonicalSchemaEvolutionError(
            "organizational record is missing"
        )

    if not isinstance(future_authority, dict):
        raise CanonicalSchemaEvolutionError(
            "future-authority record is missing"
        )

    result = CanonicalSchemaSetValidation(
        canonical_record=require_current_schema(
            "canonical_record",
            str(record.get("schema", "")),
        ),
        carrier_coalition=require_current_schema(
            "carrier_coalition",
            str(carrier_coalition.get("schema", "")),
        ),
        organizational_record=require_current_schema(
            "organizational_record",
            str(organizational.get("schema", "")),
        ),
        future_authority=require_current_schema(
            "future_authority",
            str(future_authority.get("schema", "")),
        ),
    )

    if not result.current:
        raise CanonicalSchemaEvolutionError(
            "canonical schema set is not current"
        )

    return result
