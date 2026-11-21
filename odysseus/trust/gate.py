from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from odysseus.trust.validator import TrustRegistryValidator

@dataclass(frozen=True)
class SubjectSpec:
    certification_id: str
    artifact_sha256: str
    sbom_sha256: str
    environment_baseline_sha256: str
    ops_v3_202_challenge_nonce: str

@dataclass(frozen=True)
class RegistrySnapshot:
    fetched_at_utc: datetime
    registry_clock_utc: datetime
    entries: list[dict[str, Any]]

@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    decision: str
    effective_state: str
    reason: str
    certification_id: str
    latest_sequence_number: int | None
    rollback_running_workloads: bool = False

class ReleaseGateVerifier:
    DEFAULT_CACHE_TTL_SECONDS: float = 60.0
    MAX_CLOCK_SKEW_SECONDS: float = 5.0

    def __init__(self, validator: TrustRegistryValidator, fetch_snapshot: Callable[[str], RegistrySnapshot], *, max_cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS, max_clock_skew_seconds: float = MAX_CLOCK_SKEW_SECONDS, clock: Callable[[], datetime] | None = None) -> None:
        self._validator = validator
        self._fetch_snapshot = fetch_snapshot
        self._max_ttl = max_cache_ttl_seconds
        self._max_skew = max_clock_skew_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate_release(self, subject: SubjectSpec) -> GateDecision:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        try:
            snap = self._fetch_snapshot(subject.certification_id)
        except Exception as exc:
            return GateDecision(False, 'DENY', 'UNKNOWN', f'Fail-closed: registry unavailable ({exc})', subject.certification_id, None, False)
        skew = abs((now - snap.registry_clock_utc).total_seconds())
        if skew > self._max_skew:
            return GateDecision(False, 'DENY', 'UNKNOWN', f'Fail-closed: clock skew {skew:.2f}s', subject.certification_id, None, False)
        age = (now - snap.fetched_at_utc).total_seconds()
        if age < -0.05 or age > self._max_ttl:
            return GateDecision(False, 'DENY', 'UNKNOWN', f'Fail-closed: snapshot age {age:.2f}s exceeds TTL', subject.certification_id, None, False)
        if not snap.entries:
            return GateDecision(False, 'DENY', 'UNKNOWN', 'Fail-closed: no certification records', subject.certification_id, None, False)
        prev = None
        for entry in snap.entries:
            try:
                self._validator.validate_entry(entry, previous_entry=prev)
            except Exception as exc:
                return GateDecision(False, 'DENY', 'INVALID_CHAIN', f'Fail-closed: chain error ({exc})', subject.certification_id, None, False)
            prev = entry
        lp = prev['payload']
        seq, state, b = lp['sequence_number'], lp['transition']['to_state'], lp['subject_binding']
        if (b['artifact_sha256'], b['sbom_sha256'], b['environment_baseline_sha256'], b['ops_v3_202_challenge_nonce']) != (subject.artifact_sha256, subject.sbom_sha256, subject.environment_baseline_sha256, subject.ops_v3_202_challenge_nonce):
            return GateDecision(False, 'DENY', state, 'Fail-closed: subject binding mismatch', subject.certification_id, seq, False)
        if state != 'CERTIFIED':
            return GateDecision(False, 'DENY', state, f'Fail-closed: state is {state}', subject.certification_id, seq, False)
        return GateDecision(True, 'ALLOW', 'CERTIFIED', 'Verified active CERTIFIED chain', subject.certification_id, seq, False)
