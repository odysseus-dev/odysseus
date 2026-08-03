# ============================================================
# src/rcaf/canonical_store.py
# ============================================================

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.rcaf.canonical_ledger import (
    RCAF_CANONICAL_CAPABILITIES,
    RCAF_CANONICAL_LEDGER_SCHEMA,
    RCAFFullFrameworkRecord,
)

from src.rcaf.carrier_coalition import (
    COMPONENT_ROLE_NAMES,
    COUNTERFACTUAL_ARMS,
    FUTURE_FREEDOM_COMPONENT_NAMES,
    RCAF_CARRIER_COALITION_SCHEMA,
    ScaffoldDependenceEvidence,
    TURBULENCE_CLASSES,
    TURBULENCE_COMPONENT_NAMES,
)

from src.rcaf.future_authority_ledger import (
    RCAF_FUTURE_AUTHORITY_SCHEMA,
)

from src.rcaf.canonical_evolution import (
    CanonicalSchemaEvolutionError,
    validate_canonical_record_schema_set,
)


RCAF_CANONICAL_STORE_SCHEMA = (
    "RCAF-CANONICAL-STORE-0.1"
)

RCAF_CANONICAL_ENTRY_SCHEMA = (
    "RCAF-CANONICAL-STORE-ENTRY-0.1"
)

RCAF_CANONICAL_MANIFEST_SCHEMA = (
    "RCAF-CANONICAL-STORE-MANIFEST-0.1"
)

RCAF_FORENSIC_QUARANTINE_SCHEMA = (
    "RCAF-FORENSIC-QUARANTINE-0.1"
)

RCAF_FORENSIC_QUARANTINE_MARKER_SCHEMA = (
    "RCAF-FORENSIC-QUARANTINE-MARKER-0.1"
)

ZERO_SHA256 = "0" * 64

_LEDGER_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
)

_SEGMENT_PATTERN = re.compile(
    r"^segment-(\d{6})\.jsonl$"
)

_SHA256_PATTERN = re.compile(
    r"^[0-9a-f]{64}$"
)


class CanonicalStoreError(RuntimeError):
    """Base error for the durable canonical RCAF store."""


class CanonicalStoreIntegrityError(
    CanonicalStoreError
):
    """Raised when ledger continuity or integrity is invalid."""


class CanonicalStoreSecurityError(
    CanonicalStoreError
):
    """Raised when a filesystem safety contract is violated."""


class CanonicalStoreQuarantinedError(
    CanonicalStoreError
):
    """Raised when a quarantined store is used normally."""


@dataclass(frozen=True)
class CanonicalAppendReceipt:
    ledger_id: str
    sequence_index: int
    segment_index: int
    entry_sha256: str
    previous_entry_sha256: str
    record_count: int
    segment_count: int
    durable_storage_write: bool
    authority_posture: str


@dataclass(frozen=True)
class CanonicalReplayResult:
    ledger_id: str
    record_count: int
    segment_count: int
    last_sequence_index: int | None
    last_entry_sha256: str
    records: tuple[dict[str, Any], ...]
    integrity_verified: bool
    deterministic_replay_verified: bool
    recovered_trailing_bytes: int
    repaired_terminal_newline: bool
    authority_posture: str


@dataclass(frozen=True)
class ForensicQuarantineReceipt:
    case_id: str
    ledger_id: str
    reason_code: str
    case_directory: str
    captured_entry_count: int
    copied_file_count: int
    forensic_manifest_sha256: str
    append_blocked: bool
    authority_posture: str


@dataclass(frozen=True)
class _ScanResult:
    ledger_id: str
    entries: tuple[dict[str, Any], ...]
    segment_entry_counts: tuple[int, ...]
    recovered_trailing_bytes: int
    repaired_terminal_newline: bool

    @property
    def record_count(
        self,
    ) -> int:
        return len(self.entries)

    @property
    def segment_count(
        self,
    ) -> int:
        return len(self.segment_entry_counts)

    @property
    def last_entry_sha256(
        self,
    ) -> str:
        if not self.entries:
            return ZERO_SHA256

        return str(
            self.entries[-1]["entry_sha256"]
        )


def _canonical_bytes(
    value: Any,
) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(
    value: bytes,
) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_ledger_id(
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _LEDGER_ID_PATTERN.fullmatch(
        normalized
    ):
        raise CanonicalStoreError(
            "ledger_id must contain only letters, "
            "numbers, '.', '_' or '-'"
        )

    return normalized


def _require_sha256(
    field_name: str,
    value: str,
) -> str:
    normalized = str(value).strip()

    if not _SHA256_PATTERN.fullmatch(
        normalized
    ):
        raise CanonicalStoreIntegrityError(
            f"{field_name} must be lowercase SHA-256"
        )

    return normalized


def _ensure_private_directory(
    root: Path,
) -> None:
    root = Path(root)

    try:
        root.mkdir(
            parents=True,
            mode=0o700,
            exist_ok=True,
        )
    except OSError as exc:
        raise CanonicalStoreSecurityError(
            "unable to initialize ledger root: "
            f"{type(exc).__name__}"
        ) from exc

    try:
        metadata = os.lstat(
            root
        )
    except OSError as exc:
        raise CanonicalStoreSecurityError(
            "unable to inspect ledger root: "
            f"{type(exc).__name__}"
        ) from exc

    if stat.S_ISLNK(
        metadata.st_mode
    ):
        raise CanonicalStoreSecurityError(
            "ledger root must not be a symbolic link"
        )

    if not stat.S_ISDIR(
        metadata.st_mode
    ):
        raise CanonicalStoreSecurityError(
            "ledger root must be a directory"
        )

    flags = (
        os.O_RDONLY
        | getattr(
            os,
            "O_DIRECTORY",
            0,
        )
        | getattr(
            os,
            "O_CLOEXEC",
            0,
        )
        | getattr(
            os,
            "O_NOFOLLOW",
            0,
        )
    )

    try:
        fd = os.open(
            root,
            flags,
        )
    except OSError as exc:
        raise CanonicalStoreSecurityError(
            "unable to safely open ledger root: "
            f"{type(exc).__name__}"
        ) from exc

    try:
        opened_metadata = os.fstat(
            fd
        )

        if not stat.S_ISDIR(
            opened_metadata.st_mode
        ):
            raise CanonicalStoreSecurityError(
                "opened ledger root must be a directory"
            )

        os.fchmod(
            fd,
            0o700,
        )

    finally:
        os.close(
            fd
        )

def _open_private_regular_file(
    path: Path,
    flags: int,
    *,
    mode: int = 0o600,
) -> int:
    safe_flags = (
        flags
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    try:
        fd = os.open(
            path,
            safe_flags,
            mode,
        )
    except OSError as exc:
        raise CanonicalStoreSecurityError(
            f"unable to safely open {path.name}: "
            f"{type(exc).__name__}"
        ) from exc

    try:
        metadata = os.fstat(fd)

        if not stat.S_ISREG(
            metadata.st_mode
        ):
            raise CanonicalStoreSecurityError(
                f"{path.name} must be a regular file"
            )

        if metadata.st_nlink != 1:
            raise CanonicalStoreSecurityError(
                f"{path.name} must have exactly one link"
            )

        os.fchmod(fd, 0o600)

    except Exception:
        os.close(fd)
        raise

    return fd


def _write_all(
    fd: int,
    data: bytes,
) -> None:
    offset = 0

    while offset < len(data):
        written = os.write(
            fd,
            data[offset:],
        )

        if written <= 0:
            raise CanonicalStoreError(
                "short write while appending canonical ledger"
            )

        offset += written


def _read_all(
    fd: int,
) -> bytes:
    os.lseek(
        fd,
        0,
        os.SEEK_SET,
    )

    chunks = []

    while True:
        chunk = os.read(
            fd,
            1024 * 1024,
        )

        if not chunk:
            break

        chunks.append(chunk)

    return b"".join(chunks)


def _fsync_directory(
    root: Path,
) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )

    fd = os.open(
        root,
        flags,
    )

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_private_json_atomic(
    path: Path,
    value: dict[str, Any],
) -> None:
    root = path.parent

    if path.exists():
        metadata = os.lstat(
            path
        )

        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(
                metadata.st_mode
            )
            or metadata.st_nlink != 1
        ):
            raise CanonicalStoreSecurityError(
                f"{path.name} is unsafe"
            )

    temporary_path = root / (
        f".{path.name}.{os.getpid()}."
        f"{uuid.uuid4().hex}.tmp"
    )

    fd = _open_private_regular_file(
        temporary_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL,
    )

    try:
        _write_all(
            fd,
            _canonical_bytes(value)
            + b"\n",
        )

        os.fsync(fd)

    finally:
        os.close(fd)

    os.replace(
        temporary_path,
        path,
    )

    os.chmod(
        path,
        0o600,
    )

    _fsync_directory(
        root
    )


def _quarantine_marker_path(
    root: Path,
) -> Path:
    return root / "quarantine.json"


def _read_quarantine_marker(
    root: Path,
) -> dict[str, Any] | None:
    path = _quarantine_marker_path(
        root
    )

    if not path.exists():
        return None

    fd = _open_private_regular_file(
        path,
        os.O_RDONLY,
    )

    try:
        data = _read_all(
            fd
        )
    finally:
        os.close(fd)

    try:
        marker = json.loads(
            data.decode("utf-8")
        )
    except Exception as exc:
        raise CanonicalStoreSecurityError(
            "quarantine marker is invalid"
        ) from exc

    if (
        not isinstance(marker, dict)
        or marker.get("schema")
        != RCAF_FORENSIC_QUARANTINE_MARKER_SCHEMA
        or marker.get("append_blocked")
        is not True
    ):
        raise CanonicalStoreSecurityError(
            "quarantine marker contract is invalid"
        )

    return marker


def _assert_store_not_quarantined(
    root: Path,
    *,
    ledger_id: str,
) -> None:
    marker = _read_quarantine_marker(
        root
    )

    if marker is None:
        return

    if marker.get("ledger_id") != ledger_id:
        raise CanonicalStoreSecurityError(
            "quarantine marker ledger mismatch"
        )

    raise CanonicalStoreQuarantinedError(
        "canonical store is quarantined: "
        f"{marker.get('case_id', 'unknown')}"
    )


def _segment_path(
    root: Path,
    segment_index: int,
) -> Path:
    return root / (
        f"segment-{segment_index:06d}.jsonl"
    )


def _segment_indexes(
    root: Path,
) -> tuple[int, ...]:
    indexes = []

    for entry in os.scandir(root):
        match = _SEGMENT_PATTERN.fullmatch(
            entry.name
        )

        if match is None:
            continue

        indexes.append(
            int(match.group(1))
        )

    indexes.sort()

    if indexes and indexes != list(
        range(indexes[-1] + 1)
    ):
        raise CanonicalStoreIntegrityError(
            "segment indexes must be contiguous from zero"
        )

    return tuple(indexes)


def _entry_body(
    entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in entry.items()
        if key != "entry_sha256"
    }


def _validate_carrier_coalition_dict(
    carrier: Any,
) -> dict[str, Any]:
    if (
        not isinstance(carrier, dict)
        or carrier.get("schema")
        != RCAF_CARRIER_COALITION_SCHEMA
    ):
        raise CanonicalStoreIntegrityError(
            "stored canonical record lacks the required "
            "carrier-coalition bundle"
        )

    if (
        carrier.get("raw_content_stored")
        is not False
        or carrier.get(
            "content_fingerprint_stored"
        )
        is not False
    ):
        raise CanonicalStoreIntegrityError(
            "carrier-coalition bundle violates privacy"
        )

    required_objects = (
        "coalition",
        "scaffold_dependence",
        "scaffold_release",
        "future_freedom",
        "matched_experiment",
        "equivalence_class",
        "nomination",
        "authority_contract",
    )

    for field_name in required_objects:
        if not isinstance(
            carrier.get(field_name),
            dict,
        ):
            raise CanonicalStoreIntegrityError(
                f"carrier-coalition field {field_name} "
                "must be an object"
            )

    required_nonempty_lists = (
        "validity_relations",
        "role_evidence",
        "turbulence_channels",
    )

    for field_name in required_nonempty_lists:
        value = carrier.get(field_name)

        if not isinstance(value, list) or not value:
            raise CanonicalStoreIntegrityError(
                f"carrier-coalition field {field_name} "
                "must be a nonempty list"
            )

    coalition = carrier["coalition"]
    nomination = carrier["nomination"]
    authority_contract = carrier["authority_contract"]
    matched_experiment = carrier["matched_experiment"]
    scaffold_release = carrier["scaffold_release"]

    coalition_id = coalition.get(
        "coalition_id"
    )

    if (
        not isinstance(coalition_id, str)
        or not coalition_id.strip()
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition lacks coalition_id"
        )

    if (
        coalition.get("observer_only")
        is not True
        or coalition.get("authority_eligible")
        is not False
        or coalition.get("no_auto_promotion")
        is not True
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition violates "
            "observer-only authority"
        )

    for relation in carrier[
        "validity_relations"
    ]:
        if (
            not isinstance(relation, dict)
            or relation.get("observer_only")
            is not True
            or relation.get("authority_granted")
            is not False
        ):
            raise CanonicalStoreIntegrityError(
                "carrier validity relation violates "
                "observer-only authority"
            )

    if (
        scaffold_release.get("observer_only")
        is not True
        or scaffold_release.get(
            "authority_granted"
        )
        is not False
    ):
        raise CanonicalStoreIntegrityError(
            "scaffold release evidence cannot "
            "grant authority"
        )

    nomination_safeguards = (
        "frozen_criteria",
        "independent_evaluator",
        "reversible",
        "observer_only",
        "authority_withheld",
    )

    if any(
        nomination.get(field_name)
        is not True
        for field_name
        in nomination_safeguards
    ):
        raise CanonicalStoreIntegrityError(
            "carrier nomination safeguards failed"
        )

    if (
        authority_contract.get(
            "authority_status"
        )
        != "observe_only"
        or authority_contract.get(
            "external_causal_authority"
        )
        is not False
        or authority_contract.get(
            "self_modification_authority"
        )
        is not False
        or authority_contract.get(
            "no_auto_promotion"
        )
        is not True
    ):
        raise CanonicalStoreIntegrityError(
            "carrier pruning contract violates "
            "observe-only authority"
        )

    release_criteria_ids = (
        authority_contract.get(
            "release_criteria_ids"
        )
    )

    if (
        not isinstance(
            release_criteria_ids,
            list,
        )
        or not release_criteria_ids
    ):
        raise CanonicalStoreIntegrityError(
            "carrier pruning contract lacks "
            "release criteria"
        )

    rollback_checkpoint_id = (
        authority_contract.get(
            "rollback_checkpoint_id"
        )
    )

    if (
        not isinstance(
            rollback_checkpoint_id,
            str,
        )
        or not rollback_checkpoint_id.strip()
    ):
        raise CanonicalStoreIntegrityError(
            "carrier pruning contract lacks "
            "rollback checkpoint"
        )

    for field_name in (
        "maximum_sparsity",
        "maximum_step_removal",
    ):
        value = authority_contract.get(
            field_name
        )

        if (
            isinstance(value, bool)
            or not isinstance(
                value,
                (int, float),
            )
            or not 0.0
            <= float(value)
            <= 1.0
        ):
            raise CanonicalStoreIntegrityError(
                f"carrier pruning contract field "
                f"{field_name} must be within [0, 1]"
            )

    recovery_budget_steps = (
        authority_contract.get(
            "recovery_budget_steps"
        )
    )

    if (
        isinstance(
            recovery_budget_steps,
            bool,
        )
        or not isinstance(
            recovery_budget_steps,
            int,
        )
        or recovery_budget_steps <= 0
    ):
        raise CanonicalStoreIntegrityError(
            "carrier pruning recovery budget "
            "must be positive"
        )

    experiment_safeguards = (
        "independent_evaluator",
        "frozen_acceptance_criteria",
        "matched_compute_budget",
        "multiple_seeds",
        "observer_only",
    )

    if any(
        matched_experiment.get(
            field_name
        )
        is not True
        for field_name
        in experiment_safeguards
    ):
        raise CanonicalStoreIntegrityError(
            "matched carrier experiment "
            "safeguards failed"
        )

    branches = matched_experiment.get(
        "branches"
    )

    if (
        not isinstance(branches, list)
        or len(branches)
        != len(COUNTERFACTUAL_ARMS)
        or any(
            not isinstance(branch, dict)
            for branch in branches
        )
    ):
        raise CanonicalStoreIntegrityError(
            "matched carrier experiment must "
            "contain A0 through A8"
        )

    arms = tuple(
        branch.get("arm")
        for branch in branches
    )

    if (
        set(arms)
        != set(COUNTERFACTUAL_ARMS)
        or len(set(arms))
        != len(arms)
    ):
        raise CanonicalStoreIntegrityError(
            "matched carrier experiment must "
            "contain A0 through A8"
        )

    branch_ids = tuple(
        branch.get("branch_id")
        for branch in branches
    )

    if (
        any(
            not isinstance(branch_id, str)
            or not branch_id.strip()
            for branch_id in branch_ids
        )
        or len(set(branch_ids))
        != len(branch_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "matched carrier experiment contains "
            "invalid or duplicate branch IDs"
        )

    linked_coalition_ids = {
        carrier[
            "scaffold_dependence"
        ].get("coalition_id"),
        scaffold_release.get(
            "coalition_id"
        ),
        carrier[
            "future_freedom"
        ].get("coalition_id"),
        nomination.get("coalition_id"),
        authority_contract.get(
            "coalition_id"
        ),
    }

    linked_coalition_ids.update(
        branch.get("coalition_id")
        for branch in branches
    )

    if linked_coalition_ids != {
        coalition_id
    }:
        raise CanonicalStoreIntegrityError(
            "carrier bundle contains inconsistent "
            "coalition links"
        )

    if (
        nomination.get(
            "matched_experiment_id"
        )
        != matched_experiment.get(
            "experiment_id"
        )
        or nomination.get(
            "acceptance_contract_id"
        )
        != matched_experiment.get(
            "acceptance_contract_id"
        )
    ):
        raise CanonicalStoreIntegrityError(
            "carrier nomination experiment "
            "linkage mismatch"
        )

    member_ids = coalition.get(
        "member_ids"
    )

    if (
        not isinstance(member_ids, list)
        or not member_ids
        or any(
            not isinstance(member_id, str)
            or not member_id.strip()
            for member_id in member_ids
        )
        or len(set(member_ids))
        != len(member_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition contains invalid "
            "member identifiers"
        )

    member_id_set = set(member_ids)

    reference_contract_id = coalition.get(
        "reference_contract_id"
    )

    if (
        not isinstance(
            reference_contract_id,
            str,
        )
        or not reference_contract_id.strip()
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition lacks a reference contract"
        )

    relation_ids = set()

    for relation in carrier[
        "validity_relations"
    ]:
        if not isinstance(relation, dict):
            raise CanonicalStoreIntegrityError(
                "carrier validity relation must "
                "be an object"
            )

        relation_id = relation.get(
            "relation_id"
        )

        if (
            not isinstance(relation_id, str)
            or not relation_id.strip()
            or relation_id in relation_ids
        ):
            raise CanonicalStoreIntegrityError(
                "carrier validity relation contains "
                "an invalid or duplicate relation ID"
            )

        relation_ids.add(relation_id)

        if (
            relation.get("source_member_id")
            not in member_id_set
            or relation.get("target_member_id")
            not in member_id_set
        ):
            raise CanonicalStoreIntegrityError(
                "carrier validity relation references "
                "a non-member"
            )

        relational_conditions = (
            "context_validated",
            "phase_compatible",
            "boundary_compatible",
            "future_consistent",
        )

        computed_valid = all(
            relation.get(field_name)
            is True
            for field_name
            in relational_conditions
        )

        if (
            computed_valid is not True
            or relation.get("valid")
            is not True
        ):
            raise CanonicalStoreIntegrityError(
                "carrier validity relation is incomplete"
            )

    scaffold_relation_ids = coalition.get(
        "scaffold_relation_ids"
    )

    if (
        not isinstance(
            scaffold_relation_ids,
            list,
        )
        or not set(
            scaffold_relation_ids
        ).issubset(relation_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition contains unresolved "
            "scaffold relation links"
        )

    role_evidence_ids = set()

    for evidence in carrier[
        "role_evidence"
    ]:
        if not isinstance(evidence, dict):
            raise CanonicalStoreIntegrityError(
                "carrier role evidence must be an object"
            )

        evidence_id = evidence.get(
            "evidence_id"
        )

        if (
            not isinstance(evidence_id, str)
            or not evidence_id.strip()
            or evidence_id
            in role_evidence_ids
        ):
            raise CanonicalStoreIntegrityError(
                "carrier role evidence contains "
                "an invalid or duplicate evidence ID"
            )

        role_evidence_ids.add(
            evidence_id
        )

        if evidence.get(
            "member_id"
        ) not in member_id_set:
            raise CanonicalStoreIntegrityError(
                "carrier role evidence references "
                "a non-member"
            )

        role_vector = evidence.get(
            "role_vector"
        )

        if (
            not isinstance(role_vector, dict)
            or len(role_vector)
            != len(COMPONENT_ROLE_NAMES)
            or set(role_vector)
            != set(COMPONENT_ROLE_NAMES)
        ):
            raise CanonicalStoreIntegrityError(
                "carrier role vectors must preserve "
                "all components"
            )

        if any(
            isinstance(value, bool)
            or not isinstance(
                value,
                (int, float),
            )
            or not 0.0
            <= float(value)
            <= 1.0
            for value
            in role_vector.values()
        ):
            raise CanonicalStoreIntegrityError(
                "carrier role-vector values must "
                "remain within [0, 1]"
            )

        if evidence.get(
            "role_assignment_nonexclusive"
        ) is not True:
            raise CanonicalStoreIntegrityError(
                "carrier component roles must remain "
                "nonexclusive"
            )

    declared_role_evidence_ids = coalition.get(
        "role_evidence_ids"
    )

    if (
        not isinstance(
            declared_role_evidence_ids,
            list,
        )
        or not set(
            declared_role_evidence_ids
        ).issubset(role_evidence_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition contains unresolved "
            "role-evidence links"
        )

    reference_contract_ids = {
        reference_contract_id,
        carrier[
            "scaffold_dependence"
        ].get("reference_contract_id"),
        carrier[
            "scaffold_release"
        ].get("reference_contract_id"),
        carrier[
            "future_freedom"
        ].get("reference_contract_id"),
    }

    reference_contract_ids.update(
        relation.get(
            "reference_contract_id"
        )
        for relation
        in carrier[
            "validity_relations"
        ]
    )

    reference_contract_ids.update(
        evidence.get(
            "reference_contract_id"
        )
        for evidence
        in carrier[
            "role_evidence"
        ]
    )

    if reference_contract_ids != {
        reference_contract_id
    }:
        raise CanonicalStoreIntegrityError(
            "carrier evidence contains inconsistent "
            "reference-contract links"
        )

    turbulence_ids = set()

    for channel in carrier[
        "turbulence_channels"
    ]:
        if not isinstance(channel, dict):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence channel must "
                "be an object"
            )

        channel_id = channel.get(
            "channel_id"
        )

        if (
            not isinstance(channel_id, str)
            or not channel_id.strip()
            or channel_id in turbulence_ids
        ):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence contains an "
                "invalid or duplicate channel ID"
            )

        turbulence_ids.add(channel_id)

        for field_name in (
            "source_organization_id",
            "target_organization_id",
        ):
            value = channel.get(field_name)

            if (
                not isinstance(value, str)
                or not value.strip()
            ):
                raise CanonicalStoreIntegrityError(
                    f"carrier turbulence field "
                    f"{field_name} must be an identifier"
                )

        if channel.get(
            "reference_contract_id"
        ) != reference_contract_id:
            raise CanonicalStoreIntegrityError(
                "carrier turbulence contains "
                "inconsistent reference-contract links"
            )

        horizon_steps = channel.get(
            "horizon_steps"
        )

        if (
            isinstance(horizon_steps, bool)
            or not isinstance(
                horizon_steps,
                int,
            )
            or horizon_steps <= 0
        ):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence horizon must "
                "be positive"
            )

        component_vector = channel.get(
            "component_vector"
        )

        if (
            not isinstance(
                component_vector,
                dict,
            )
            or len(component_vector)
            != len(TURBULENCE_COMPONENT_NAMES)
            or set(component_vector)
            != set(TURBULENCE_COMPONENT_NAMES)
        ):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence vectors must "
                "preserve all components"
            )

        if any(
            isinstance(value, bool)
            or not isinstance(
                value,
                (int, float),
            )
            or not 0.0
            <= float(value)
            <= 1.0
            for value
            in component_vector.values()
        ):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence component values "
                "must remain within [0, 1]"
            )

        turbulence_classes = channel.get(
            "turbulence_classes"
        )

        if (
            not isinstance(
                turbulence_classes,
                list,
            )
            or not turbulence_classes
            or len(set(turbulence_classes))
            != len(turbulence_classes)
            or not set(
                turbulence_classes
            ).issubset(TURBULENCE_CLASSES)
        ):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence contains "
                "unknown classes"
            )

        evidence_record_ids = channel.get(
            "evidence_record_ids"
        )

        if (
            not isinstance(
                evidence_record_ids,
                list,
            )
            or not evidence_record_ids
            or any(
                not isinstance(
                    evidence_id,
                    str,
                )
                or not evidence_id.strip()
                for evidence_id
                in evidence_record_ids
            )
            or len(set(evidence_record_ids))
            != len(evidence_record_ids)
        ):
            raise CanonicalStoreIntegrityError(
                "carrier turbulence contains "
                "invalid evidence links"
            )

        if channel.get(
            "component_preserving"
        ) is not True:
            raise CanonicalStoreIntegrityError(
                "carrier turbulence components "
                "must remain preserved"
            )

        if channel.get(
            "collapsed_score_authoritative"
        ) is not False:
            raise CanonicalStoreIntegrityError(
                "collapsed carrier turbulence score "
                "cannot be authoritative"
            )

    declared_turbulence_ids = coalition.get(
        "turbulence_channel_ids"
    )

    if (
        not isinstance(
            declared_turbulence_ids,
            list,
        )
        or not declared_turbulence_ids
        or any(
            not isinstance(channel_id, str)
            or not channel_id.strip()
            for channel_id
            in declared_turbulence_ids
        )
        or len(set(declared_turbulence_ids))
        != len(declared_turbulence_ids)
        or not set(
            declared_turbulence_ids
        ).issubset(turbulence_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition contains unresolved "
            "turbulence links"
        )

    trajectory_conditions = (
        "forward_reachable",
        "backward_consistent",
        "self_supporting",
        "support_withdrawal_verified",
        "nomination_ready",
    )

    if any(
        coalition.get(field_name)
        is not True
        for field_name
        in trajectory_conditions
    ):
        raise CanonicalStoreIntegrityError(
            "carrier coalition is not "
            "trajectory-admissible"
        )

    scaffold_dependence = carrier[
        "scaffold_dependence"
    ]

    scaffold_release = carrier[
        "scaffold_release"
    ]

    future_freedom = carrier[
        "future_freedom"
    ]

    equivalence_class = carrier[
        "equivalence_class"
    ]

    dependence_members = (
        scaffold_dependence.get(
            "scaffold_member_ids"
        )
    )

    if (
        not isinstance(
            dependence_members,
            list,
        )
        or not dependence_members
        or any(
            not isinstance(member_id, str)
            or not member_id.strip()
            for member_id
            in dependence_members
        )
        or len(set(dependence_members))
        != len(dependence_members)
        or not set(
            dependence_members
        ).issubset(member_id_set)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold dependence "
            "references invalid members"
        )

    observation_horizon = (
        scaffold_dependence.get(
            "observation_horizon"
        )
    )

    if (
        isinstance(observation_horizon, bool)
        or not isinstance(
            observation_horizon,
            int,
        )
        or observation_horizon <= 0
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold-dependence horizon "
            "must be positive"
        )

    dependence_vector = (
        scaffold_dependence.get(
            "dependence_vector"
        )
    )

    if (
        not isinstance(
            dependence_vector,
            dict,
        )
        or len(dependence_vector)
        != len(ScaffoldDependenceEvidence.DEPENDENCE_COMPONENT_NAMES)
        or set(dependence_vector)
        != set(ScaffoldDependenceEvidence.DEPENDENCE_COMPONENT_NAMES)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold-dependence vector "
            "must preserve all components"
        )

    if any(
        isinstance(value, bool)
        or not isinstance(
            value,
            (int, float),
        )
        or not 0.0
        <= float(value)
        <= 1.0
        for value
        in dependence_vector.values()
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold-dependence values "
            "must remain within [0, 1]"
        )

    if scaffold_dependence.get(
        "support_required_now"
    ) is not False:
        raise CanonicalStoreIntegrityError(
            "carrier support withdrawal remains "
            "scaffold-dependent"
        )

    dependence_evidence_ids = (
        scaffold_dependence.get(
            "evidence_record_ids"
        )
    )

    if (
        not isinstance(
            dependence_evidence_ids,
            list,
        )
        or not dependence_evidence_ids
        or any(
            not isinstance(evidence_id, str)
            or not evidence_id.strip()
            for evidence_id
            in dependence_evidence_ids
        )
        or len(set(dependence_evidence_ids))
        != len(dependence_evidence_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold dependence contains "
            "invalid evidence links"
        )

    release_members = (
        scaffold_release.get(
            "scaffold_member_ids"
        )
    )

    if (
        not isinstance(release_members, list)
        or not release_members
        or any(
            not isinstance(member_id, str)
            or not member_id.strip()
            for member_id
            in release_members
        )
        or len(set(release_members))
        != len(release_members)
        or not set(
            release_members
        ).issubset(member_id_set)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold release references "
            "invalid members"
        )

    withdrawal_horizon = (
        scaffold_release.get(
            "withdrawal_horizon"
        )
    )

    if (
        isinstance(withdrawal_horizon, bool)
        or not isinstance(
            withdrawal_horizon,
            int,
        )
        or withdrawal_horizon <= 0
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold-release horizon "
            "must be positive"
        )

    release_condition_names = (
        "immediate_performance_preserved",
        "far_horizon_performance_preserved",
        "calibration_preserved",
        "robustness_preserved",
        "optimizer_stable",
        "activation_geometry_stable",
        "persistence_verified",
        "recovery_available",
        "transfer_preserved",
        "future_freedom_preserved",
        "turbulent_debt_admissible",
    )

    computed_release_admissible = all(
        scaffold_release.get(field_name)
        is True
        for field_name
        in release_condition_names
    )

    if (
        computed_release_admissible
        is not True
        or scaffold_release.get(
            "release_admissible"
        )
        is not True
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold release is "
            "not admissible"
        )

    release_evidence_ids = (
        scaffold_release.get(
            "evidence_record_ids"
        )
    )

    if (
        not isinstance(
            release_evidence_ids,
            list,
        )
        or not release_evidence_ids
        or any(
            not isinstance(evidence_id, str)
            or not evidence_id.strip()
            for evidence_id
            in release_evidence_ids
        )
        or len(set(release_evidence_ids))
        != len(release_evidence_ids)
    ):
        raise CanonicalStoreIntegrityError(
            "carrier scaffold release contains "
            "invalid evidence links"
        )

    future_freedom_record_id = (
        future_freedom.get("record_id")
    )

    if (
        not isinstance(
            future_freedom_record_id,
            str,
        )
        or not future_freedom_record_id.strip()
        or coalition.get(
            "future_freedom_record_id"
        )
        != future_freedom_record_id
    ):
        raise CanonicalStoreIntegrityError(
            "carrier future-freedom linkage mismatch"
        )

    baseline_vector = future_freedom.get(
        "baseline_vector"
    )

    candidate_vector = future_freedom.get(
        "candidate_vector"
    )

    retention_vector = future_freedom.get(
        "retention_by_component"
    )

    for field_name, vector in (
        ("baseline_vector", baseline_vector),
        ("candidate_vector", candidate_vector),
        (
            "retention_by_component",
            retention_vector,
        ),
    ):
        if (
            not isinstance(vector, dict)
            or len(vector)
            != len(FUTURE_FREEDOM_COMPONENT_NAMES)
            or set(vector)
            != set(FUTURE_FREEDOM_COMPONENT_NAMES)
        ):
            raise CanonicalStoreIntegrityError(
                f"carrier future-freedom "
                f"{field_name} must preserve "
                "all components"
            )

        if any(
            isinstance(value, bool)
            or not isinstance(
                value,
                (int, float),
            )
            or not 0.0
            <= float(value)
            <= 1.0
            for value
            in vector.values()
        ):
            raise CanonicalStoreIntegrityError(
                "carrier future-freedom values "
                "must remain within [0, 1]"
            )

    minimum_retention_ratio = (
        future_freedom.get(
            "minimum_retention_ratio"
        )
    )

    if (
        isinstance(
            minimum_retention_ratio,
            bool,
        )
        or not isinstance(
            minimum_retention_ratio,
            (int, float),
        )
        or not 0.0
        <= float(minimum_retention_ratio)
        <= 1.0
    ):
        raise CanonicalStoreIntegrityError(
            "carrier future-freedom minimum "
            "retention must be within [0, 1]"
        )

    computed_retention = {}

    for component_name in (
        FUTURE_FREEDOM_COMPONENT_NAMES
    ):
        baseline = float(
            baseline_vector[
                component_name
            ]
        )

        candidate = float(
            candidate_vector[
                component_name
            ]
        )

        if baseline == 0.0:
            ratio = 1.0
        else:
            ratio = min(
                1.0,
                candidate / baseline,
            )

        computed_retention[
            component_name
        ] = ratio

        stored_ratio = float(
            retention_vector[
                component_name
            ]
        )

        if abs(
            stored_ratio - ratio
        ) > 1e-12:
            raise CanonicalStoreIntegrityError(
                "carrier future-freedom retention "
                "is inconsistent"
            )

    computed_preserved = all(
        ratio
        >= float(
            minimum_retention_ratio
        )
        for ratio
        in computed_retention.values()
    )

    if (
        future_freedom.get("preserved")
        is not computed_preserved
    ):
        raise CanonicalStoreIntegrityError(
            "carrier future-freedom preserved "
            "state is inconsistent"
        )

    if computed_preserved is not True:
        raise CanonicalStoreIntegrityError(
            "carrier future freedom is not preserved"
        )

    future_freedom_evidence_ids = (
        future_freedom.get(
            "evidence_record_ids"
        )
    )

    if (
        not isinstance(
            future_freedom_evidence_ids,
            list,
        )
        or not future_freedom_evidence_ids
        or any(
            not isinstance(evidence_id, str)
            or not evidence_id.strip()
            for evidence_id
            in future_freedom_evidence_ids
        )
        or len(
            set(
                future_freedom_evidence_ids
            )
        )
        != len(
            future_freedom_evidence_ids
        )
    ):
        raise CanonicalStoreIntegrityError(
            "carrier future freedom contains "
            "invalid evidence links"
        )

    equivalence_class_id = (
        equivalence_class.get("class_id")
    )

    terminal_class_id = (
        equivalence_class.get(
            "terminal_organizational_class_id"
        )
    )

    if (
        not isinstance(
            equivalence_class_id,
            str,
        )
        or not equivalence_class_id.strip()
        or not isinstance(
            terminal_class_id,
            str,
        )
        or not terminal_class_id.strip()
    ):
        raise CanonicalStoreIntegrityError(
            "carrier ticket equivalence lacks "
            "structural identifiers"
        )

    equivalence_coalition_ids = (
        equivalence_class.get(
            "coalition_ids"
        )
    )

    if (
        not isinstance(
            equivalence_coalition_ids,
            list,
        )
        or len(equivalence_coalition_ids)
        < 2
        or any(
            not isinstance(item_id, str)
            or not item_id.strip()
            for item_id
            in equivalence_coalition_ids
        )
        or len(
            set(
                equivalence_coalition_ids
            )
        )
        != len(
            equivalence_coalition_ids
        )
        or coalition_id
        not in equivalence_coalition_ids
    ):
        raise CanonicalStoreIntegrityError(
            "carrier ticket equivalence requires "
            "at least two coalitions including "
            "the primary coalition"
        )

    for field_name in (
        "invariant_function_record_ids",
        "admissible_corridor_ids",
        "evidence_record_ids",
    ):
        values = equivalence_class.get(
            field_name
        )

        if (
            not isinstance(values, list)
            or not values
            or any(
                not isinstance(item_id, str)
                or not item_id.strip()
                for item_id in values
            )
            or len(set(values))
            != len(values)
        ):
            raise CanonicalStoreIntegrityError(
                f"carrier ticket equivalence "
                f"contains invalid {field_name}"
            )

    if (
        terminal_class_id
        != coalition.get(
            "terminal_organizational_class_id"
        )
    ):
        raise CanonicalStoreIntegrityError(
            "carrier ticket equivalence "
            "terminal-class mismatch"
        )

    if (
        equivalence_class.get(
            "equivalent_terminal_organization"
        )
        is not True
        or equivalence_class.get(
            "identical_microstate_required"
        )
        is not False
    ):
        raise CanonicalStoreIntegrityError(
            "carrier ticket equivalence violates "
            "organizational equivalence"
        )

    return carrier


def _validate_framework_record_dict(
    record: Any,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise CanonicalStoreIntegrityError(
            "stored canonical record must be an object"
        )

    if (
        record.get("schema")
        != RCAF_CANONICAL_LEDGER_SCHEMA
    ):
        raise CanonicalStoreIntegrityError(
            "stored canonical record schema mismatch"
        )

    completeness = record.get(
        "completeness"
    )

    expected_capabilities = tuple(
        sorted(RCAF_CANONICAL_CAPABILITIES)
    )

    satisfied_capabilities = (
        completeness.get(
            "satisfied_capabilities"
        )
        if isinstance(completeness, dict)
        else None
    )

    if (
        not isinstance(completeness, dict)
        or completeness.get(
            "required_capability_count"
        )
        != len(expected_capabilities)
        or completeness.get(
            "satisfied_capability_count"
        )
        != len(expected_capabilities)
        or not isinstance(
            satisfied_capabilities,
            list,
        )
        or tuple(satisfied_capabilities)
        != expected_capabilities
        or completeness.get(
            "missing_capabilities"
        )
        != []
        or completeness.get("complete")
        is not True
    ):
        raise CanonicalStoreIntegrityError(
            "stored canonical record is not complete: "
            "exact capability envelope mismatch"
        )

    for field_name in (
        "raw_content_stored",
        "content_fingerprint_stored",
        "semantic_memory_authority",
        "identity_proof_established",
        "realization_authorized",
        "external_causal_authority",
        "self_modification_authority",
    ):
        if record.get(field_name) is not False:
            raise CanonicalStoreIntegrityError(
                f"stored canonical record violates {field_name}"
            )

    if (
        record.get("authority_posture")
        != "observe_only"
    ):
        raise CanonicalStoreIntegrityError(
            "store currently accepts observe-only records"
        )

    future_authority = record.get(
        "future_authority_evidence"
    )

    if (
        not isinstance(future_authority, dict)
        or future_authority.get("schema")
        != RCAF_FUTURE_AUTHORITY_SCHEMA
    ):
        raise CanonicalStoreIntegrityError(
            "stored canonical record lacks the required "
            "future-authority evidence bundle"
        )

    if (
        future_authority.get("raw_content_stored")
        is not False
        or future_authority.get(
            "content_fingerprint_stored"
        )
        is not False
    ):
        raise CanonicalStoreIntegrityError(
            "future-authority evidence violates privacy"
        )

    if (
        future_authority.get(
            "causal_authority_eligible"
        )
        is not False
    ):
        raise CanonicalStoreIntegrityError(
            "observe-only record cannot be future-authority eligible"
        )

    lifecycle = future_authority.get(
        "lifecycle"
    )

    if (
        not isinstance(lifecycle, dict)
        or lifecycle.get("authority_status")
        != "observe_only"
        or lifecycle.get("no_auto_promotion")
        is not True
    ):
        raise CanonicalStoreIntegrityError(
            "future-gate lifecycle violates observe-only storage"
        )

    carrier = _validate_carrier_coalition_dict(
        record.get("carrier_coalition")
    )

    reference_conditioning = record.get(
        "reference_conditioning"
    )

    future_gate = record.get(
        "future_gate"
    )

    if (
        not isinstance(
            reference_conditioning,
            dict,
        )
        or not isinstance(future_gate, dict)
    ):
        raise CanonicalStoreIntegrityError(
            "canonical carrier links require "
            "reference and future-gate records"
        )

    active_reference_contract_id = (
        reference_conditioning.get(
            "reference_contract_id"
        )
    )

    terminal_class_id = future_gate.get(
        "terminal_organizational_class_id"
    )

    coalition = carrier["coalition"]

    if (
        coalition.get(
            "reference_contract_id"
        )
        != active_reference_contract_id
    ):
        raise CanonicalStoreIntegrityError(
            "carrier reference contract does not "
            "match canonical reference conditioning"
        )

    if (
        coalition.get(
            "terminal_organizational_class_id"
        )
        != terminal_class_id
        or carrier[
            "equivalence_class"
        ].get(
            "terminal_organizational_class_id"
        )
        != terminal_class_id
    ):
        raise CanonicalStoreIntegrityError(
            "carrier terminal class does not "
            "match canonical future gate"
        )

    try:
        validate_canonical_record_schema_set(
            record
        )
    except CanonicalSchemaEvolutionError as exc:
        raise CanonicalStoreIntegrityError(
            "schema-evolution policy rejected the record"
        ) from exc

    return record


def _decode_segment(
    path: Path,
    *,
    recover_trailing: bool,
) -> tuple[
    tuple[dict[str, Any], ...],
    int,
    bool,
]:
    fd = _open_private_regular_file(
        path,
        os.O_RDWR,
    )

    recovered_bytes = 0
    repaired_newline = False

    try:
        data = _read_all(fd)

        if data and not data.endswith(b"\n"):
            last_newline = data.rfind(b"\n")
            fragment_start = last_newline + 1
            fragment = data[fragment_start:]

            try:
                json.loads(
                    fragment.decode("utf-8")
                )

            except Exception as exc:
                if not recover_trailing:
                    raise CanonicalStoreIntegrityError(
                        "invalid trailing partial record"
                    ) from exc

                recovered_bytes = len(fragment)

                os.ftruncate(
                    fd,
                    fragment_start,
                )

                os.fsync(fd)

                data = data[:fragment_start]

            else:
                if not recover_trailing:
                    raise CanonicalStoreIntegrityError(
                        "terminal record is missing newline"
                    )

                os.lseek(
                    fd,
                    0,
                    os.SEEK_END,
                )

                _write_all(
                    fd,
                    b"\n",
                )

                os.fsync(fd)

                data += b"\n"
                repaired_newline = True

        entries = []

        for line_number, raw_line in enumerate(
            data.splitlines(),
            start=1,
        ):
            if not raw_line:
                raise CanonicalStoreIntegrityError(
                    f"empty record at line {line_number}"
                )

            try:
                entry = json.loads(
                    raw_line.decode("utf-8")
                )
            except Exception as exc:
                raise CanonicalStoreIntegrityError(
                    f"invalid JSON at line {line_number}"
                ) from exc

            if not isinstance(entry, dict):
                raise CanonicalStoreIntegrityError(
                    f"entry {line_number} must be an object"
                )

            entries.append(entry)

        return (
            tuple(entries),
            recovered_bytes,
            repaired_newline,
        )

    finally:
        os.close(fd)


def _validate_entry(
    entry: dict[str, Any],
    *,
    expected_ledger_id: str,
    expected_segment_index: int,
    expected_sequence_index: int,
    expected_previous_sha256: str,
) -> None:
    if (
        entry.get("schema")
        != RCAF_CANONICAL_ENTRY_SCHEMA
    ):
        raise CanonicalStoreIntegrityError(
            "entry schema mismatch"
        )

    if (
        entry.get("ledger_id")
        != expected_ledger_id
    ):
        raise CanonicalStoreIntegrityError(
            "entry ledger_id mismatch"
        )

    if (
        entry.get("segment_index")
        != expected_segment_index
    ):
        raise CanonicalStoreIntegrityError(
            "entry segment_index mismatch"
        )

    if (
        entry.get("sequence_index")
        != expected_sequence_index
    ):
        raise CanonicalStoreIntegrityError(
            "entry sequence continuity failure"
        )

    previous = _require_sha256(
        "previous_entry_sha256",
        entry.get(
            "previous_entry_sha256",
            "",
        ),
    )

    if previous != expected_previous_sha256:
        raise CanonicalStoreIntegrityError(
            "entry hash-chain continuity failure"
        )

    stored_hash = _require_sha256(
        "entry_sha256",
        entry.get(
            "entry_sha256",
            "",
        ),
    )

    calculated_hash = _sha256(
        _canonical_bytes(
            _entry_body(entry)
        )
    )

    if stored_hash != calculated_hash:
        raise CanonicalStoreIntegrityError(
            "entry SHA-256 mismatch"
        )

    _validate_framework_record_dict(
        entry.get("record")
    )


def _scan_store(
    root: Path,
    *,
    ledger_id: str,
    recover_trailing: bool,
) -> _ScanResult:
    segment_indexes = _segment_indexes(
        root
    )

    entries = []
    segment_counts = []
    previous_sha256 = ZERO_SHA256
    expected_sequence = 0
    record_ids = set()
    recovered_bytes = 0
    repaired_newline = False

    for position, segment_index in enumerate(
        segment_indexes
    ):
        is_last = (
            position
            == len(segment_indexes) - 1
        )

        (
            segment_entries,
            segment_recovered,
            segment_repaired,
        ) = _decode_segment(
            _segment_path(
                root,
                segment_index,
            ),
            recover_trailing=(
                recover_trailing
                and is_last
            ),
        )

        if (
            not is_last
            and not segment_entries
        ):
            raise CanonicalStoreIntegrityError(
                "non-terminal segment must not be empty"
            )

        recovered_bytes += segment_recovered
        repaired_newline = (
            repaired_newline
            or segment_repaired
        )

        for entry in segment_entries:
            _validate_entry(
                entry,
                expected_ledger_id=ledger_id,
                expected_segment_index=(
                    segment_index
                ),
                expected_sequence_index=(
                    expected_sequence
                ),
                expected_previous_sha256=(
                    previous_sha256
                ),
            )

            record = entry["record"]
            record_id = record.get(
                "record_id"
            )

            if record_id in record_ids:
                raise CanonicalStoreIntegrityError(
                    "duplicate canonical record_id"
                )

            record_ids.add(record_id)

            previous_sha256 = entry[
                "entry_sha256"
            ]

            expected_sequence += 1
            entries.append(entry)

        segment_counts.append(
            len(segment_entries)
        )

    return _ScanResult(
        ledger_id=ledger_id,
        entries=tuple(entries),
        segment_entry_counts=tuple(
            segment_counts
        ),
        recovered_trailing_bytes=(
            recovered_bytes
        ),
        repaired_terminal_newline=(
            repaired_newline
        ),
    )


def _manifest_dict(
    scan: _ScanResult,
) -> dict[str, Any]:
    return {
        "schema": (
            RCAF_CANONICAL_MANIFEST_SCHEMA
        ),
        "ledger_id": scan.ledger_id,
        "record_count": scan.record_count,
        "segment_count": scan.segment_count,
        "last_sequence_index": (
            scan.record_count - 1
            if scan.record_count
            else None
        ),
        "last_entry_sha256": (
            scan.last_entry_sha256
        ),
        "segment_entry_counts": list(
            scan.segment_entry_counts
        ),
        "authority_posture": "observe_only",
        "raw_content_stored": False,
        "content_fingerprint_stored": False,
    }


def _write_manifest_atomic(
    root: Path,
    scan: _ScanResult,
) -> None:
    manifest_path = (
        root / "manifest.json"
    )

    if manifest_path.exists():
        metadata = os.lstat(
            manifest_path
        )

        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(
                metadata.st_mode
            )
            or metadata.st_nlink != 1
        ):
            raise CanonicalStoreSecurityError(
                "existing manifest is unsafe"
            )

    temporary_path = root / (
        f".manifest.{os.getpid()}."
        f"{uuid.uuid4().hex}.tmp"
    )

    fd = _open_private_regular_file(
        temporary_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL,
    )

    try:
        data = (
            _canonical_bytes(
                _manifest_dict(scan)
            )
            + b"\n"
        )

        _write_all(
            fd,
            data,
        )

        os.fsync(fd)

    finally:
        os.close(fd)

    os.replace(
        temporary_path,
        manifest_path,
    )

    os.chmod(
        manifest_path,
        0o600,
    )

    _fsync_directory(root)


def _lock_store(
    root: Path,
) -> int:
    lock_path = root / ".lock"

    fd = _open_private_regular_file(
        lock_path,
        os.O_RDWR | os.O_CREAT,
    )

    try:
        fcntl.flock(
            fd,
            fcntl.LOCK_EX,
        )
    except Exception:
        os.close(fd)
        raise

    return fd


def _unlock_store(
    fd: int,
) -> None:
    try:
        fcntl.flock(
            fd,
            fcntl.LOCK_UN,
        )
    finally:
        os.close(fd)


def quarantine_canonical_store(
    root: str | Path,
    *,
    ledger_id: str,
    case_id: str,
    reason_code: str,
) -> ForensicQuarantineReceipt:
    ledger_id = _require_ledger_id(
        ledger_id
    )

    case_id = _require_ledger_id(
        case_id
    )

    reason_code = _require_ledger_id(
        reason_code
    )

    root = Path(root)

    _ensure_private_directory(
        root
    )

    lock_fd = _lock_store(
        root
    )

    try:
        _assert_store_not_quarantined(
            root,
            ledger_id=ledger_id,
        )

        forensic_root = (
            root.parent
            / f"{root.name}.forensics"
        )

        _ensure_private_directory(
            forensic_root
        )

        case_directory = (
            forensic_root
            / case_id
        )

        if case_directory.exists():
            raise CanonicalStoreError(
                "forensic case already exists"
            )

        case_directory.mkdir(
            mode=0o700,
        )

        os.chmod(
            case_directory,
            0o700,
        )

        captured_entries = []
        copied_file_count = 0

        for entry in sorted(
            os.scandir(root),
            key=lambda item: item.name,
        ):
            if entry.name == "quarantine.json":
                continue

            source_path = (
                root
                / entry.name
            )

            metadata = os.lstat(
                source_path
            )

            base = {
                "name": entry.name,
                "source_mode": oct(
                    stat.S_IMODE(
                        metadata.st_mode
                    )
                ),
                "source_nlink": (
                    metadata.st_nlink
                ),
            }

            if (
                stat.S_ISREG(
                    metadata.st_mode
                )
                and metadata.st_nlink == 1
            ):
                source_fd = (
                    _open_private_regular_file(
                        source_path,
                        os.O_RDONLY,
                    )
                )

                try:
                    data = _read_all(
                        source_fd
                    )
                finally:
                    os.close(
                        source_fd
                    )

                destination = (
                    case_directory
                    / entry.name
                )

                destination_fd = (
                    _open_private_regular_file(
                        destination,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL,
                    )
                )

                try:
                    _write_all(
                        destination_fd,
                        data,
                    )

                    os.fsync(
                        destination_fd
                    )

                finally:
                    os.close(
                        destination_fd
                    )

                captured_entries.append(
                    {
                        **base,
                        "entry_type": "regular",
                        "copied": True,
                        "size_bytes": len(data),
                        "sha256": _sha256(data),
                    }
                )

                copied_file_count += 1

            elif stat.S_ISLNK(
                metadata.st_mode
            ):
                captured_entries.append(
                    {
                        **base,
                        "entry_type": "symlink",
                        "copied": False,
                        "target_followed": False,
                    }
                )

            elif stat.S_ISDIR(
                metadata.st_mode
            ):
                captured_entries.append(
                    {
                        **base,
                        "entry_type": "directory",
                        "copied": False,
                    }
                )

            else:
                captured_entries.append(
                    {
                        **base,
                        "entry_type": "unsupported",
                        "copied": False,
                    }
                )

        forensic_manifest = {
            "schema": (
                RCAF_FORENSIC_QUARANTINE_SCHEMA
            ),
            "case_id": case_id,
            "ledger_id": ledger_id,
            "reason_code": reason_code,
            "source_store_name": root.name,
            "captured_entry_count": len(
                captured_entries
            ),
            "copied_file_count": (
                copied_file_count
            ),
            "entries": captured_entries,
            "forensic_bytes_preserved": True,
            "content_interpretation_performed": False,
            "repair_performed": False,
            "authority_posture": "observe_only",
        }

        forensic_manifest_path = (
            case_directory
            / "forensic_manifest.json"
        )

        _write_private_json_atomic(
            forensic_manifest_path,
            forensic_manifest,
        )

        forensic_manifest_sha256 = (
            _sha256(
                forensic_manifest_path
                .read_bytes()
            )
        )

        marker = {
            "schema": (
                RCAF_FORENSIC_QUARANTINE_MARKER_SCHEMA
            ),
            "case_id": case_id,
            "ledger_id": ledger_id,
            "reason_code": reason_code,
            "forensic_manifest_sha256": (
                forensic_manifest_sha256
            ),
            "append_blocked": True,
            "normal_validation_blocked": True,
            "repair_performed": False,
            "authority_posture": "observe_only",
        }

        _write_private_json_atomic(
            _quarantine_marker_path(
                root
            ),
            marker,
        )

        _fsync_directory(
            case_directory
        )

        _fsync_directory(
            forensic_root
        )

        return ForensicQuarantineReceipt(
            case_id=case_id,
            ledger_id=ledger_id,
            reason_code=reason_code,
            case_directory=str(
                case_directory
            ),
            captured_entry_count=len(
                captured_entries
            ),
            copied_file_count=(
                copied_file_count
            ),
            forensic_manifest_sha256=(
                forensic_manifest_sha256
            ),
            append_blocked=True,
            authority_posture="observe_only",
        )

    finally:
        _unlock_store(
            lock_fd
        )


def append_canonical_record(
    root: str | Path,
    *,
    ledger_id: str,
    record: RCAFFullFrameworkRecord,
    max_records_per_segment: int = 256,
) -> CanonicalAppendReceipt:
    ledger_id = _require_ledger_id(
        ledger_id
    )

    if (
        isinstance(
            max_records_per_segment,
            bool,
        )
        or not isinstance(
            max_records_per_segment,
            int,
        )
        or max_records_per_segment <= 0
    ):
        raise CanonicalStoreError(
            "max_records_per_segment must be a positive integer"
        )

    if not isinstance(
        record,
        RCAFFullFrameworkRecord,
    ):
        raise CanonicalStoreError(
            "record must be RCAFFullFrameworkRecord"
        )

    record_dict = record.to_dict()

    _validate_framework_record_dict(
        record_dict
    )

    root = Path(root)

    _ensure_private_directory(root)

    lock_fd = _lock_store(root)

    try:
        _assert_store_not_quarantined(
            root,
            ledger_id=ledger_id,
        )

        scan = _scan_store(
            root,
            ledger_id=ledger_id,
            recover_trailing=True,
        )

        existing_record_ids = {
            entry["record"]["record_id"]
            for entry in scan.entries
        }

        if record.record_id in existing_record_ids:
            raise CanonicalStoreIntegrityError(
                "duplicate canonical record_id"
            )

        if not scan.segment_entry_counts:
            segment_index = 0

        elif (
            scan.segment_entry_counts[-1]
            >= max_records_per_segment
        ):
            segment_index = (
                scan.segment_count
            )

        else:
            segment_index = (
                scan.segment_count - 1
            )

        sequence_index = scan.record_count
        previous_sha256 = (
            scan.last_entry_sha256
        )

        body = {
            "schema": (
                RCAF_CANONICAL_ENTRY_SCHEMA
            ),
            "ledger_id": ledger_id,
            "segment_index": segment_index,
            "sequence_index": (
                sequence_index
            ),
            "previous_entry_sha256": (
                previous_sha256
            ),
            "record": record_dict,
        }

        entry_sha256 = _sha256(
            _canonical_bytes(body)
        )

        entry = {
            **body,
            "entry_sha256": entry_sha256,
        }

        segment_path = _segment_path(
            root,
            segment_index,
        )

        fd = _open_private_regular_file(
            segment_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND,
        )

        try:
            _write_all(
                fd,
                _canonical_bytes(entry)
                + b"\n",
            )

            os.fsync(fd)

        finally:
            os.close(fd)

        verified = _scan_store(
            root,
            ledger_id=ledger_id,
            recover_trailing=False,
        )

        if (
            verified.record_count
            != sequence_index + 1
            or verified.last_entry_sha256
            != entry_sha256
        ):
            raise CanonicalStoreIntegrityError(
                "post-append verification failed"
            )

        _write_manifest_atomic(
            root,
            verified,
        )

        return CanonicalAppendReceipt(
            ledger_id=ledger_id,
            sequence_index=sequence_index,
            segment_index=segment_index,
            entry_sha256=entry_sha256,
            previous_entry_sha256=(
                previous_sha256
            ),
            record_count=(
                verified.record_count
            ),
            segment_count=(
                verified.segment_count
            ),
            durable_storage_write=True,
            authority_posture=(
                "observe_only"
            ),
        )

    finally:
        _unlock_store(lock_fd)


def validate_and_replay_canonical_store(
    root: str | Path,
    *,
    ledger_id: str,
    recover_trailing: bool = False,
    rebuild_manifest: bool = True,
    allow_quarantined: bool = False,
) -> CanonicalReplayResult:
    ledger_id = _require_ledger_id(
        ledger_id
    )

    root = Path(root)

    _ensure_private_directory(root)

    lock_fd = _lock_store(root)

    try:
        if not allow_quarantined:
            _assert_store_not_quarantined(
                root,
                ledger_id=ledger_id,
            )

        scan = _scan_store(
            root,
            ledger_id=ledger_id,
            recover_trailing=(
                recover_trailing
            ),
        )

        records = tuple(
            entry["record"]
            for entry in scan.entries
        )

        first_replay = tuple(
            _canonical_bytes(record)
            for record in records
        )

        second_replay = tuple(
            _canonical_bytes(record)
            for record in records
        )

        deterministic = (
            first_replay == second_replay
        )

        if rebuild_manifest:
            _write_manifest_atomic(
                root,
                scan,
            )

        return CanonicalReplayResult(
            ledger_id=ledger_id,
            record_count=(
                scan.record_count
            ),
            segment_count=(
                scan.segment_count
            ),
            last_sequence_index=(
                scan.record_count - 1
                if scan.record_count
                else None
            ),
            last_entry_sha256=(
                scan.last_entry_sha256
            ),
            records=records,
            integrity_verified=True,
            deterministic_replay_verified=(
                deterministic
            ),
            recovered_trailing_bytes=(
                scan.recovered_trailing_bytes
            ),
            repaired_terminal_newline=(
                scan.repaired_terminal_newline
            ),
            authority_posture=(
                "observe_only"
            ),
        )

    finally:
        _unlock_store(lock_fd)
