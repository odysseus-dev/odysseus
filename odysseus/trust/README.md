# OdyAI Trust Registry & Release Gate (`odysseus.trust`)

Implementation of **OPS-V3-203 (Certification Revocation and Trust Withdrawal)**, extending **OPS-V3-202** cryptographically bound, single-use certification with tamper-evident trust withdrawal and fail-closed release enforcement.

> **Security Invariant:** Revoking or suspending a certification withdraws authorization for **future** release decisions (`rollback_running_workloads=False`). It never automatically uninstalls, disables, or rolls back live production workloads, and a previously valid signature never overrides a current revocation or suspension entry.

---

## Architecture & Modules

| File | Responsibility |
| :--- | :--- |
| `schemas/ops-v3-203/trust-registry-entry.v1.json` | JSON Schema (Draft 2020-12) defining the dSSE-style state-transition envelope, subject bindings, trigger classes, and evidence references. |
| `odysseus/trust/validator.py` | `TrustRegistryValidator`: enforces schema compliance, RFC 8785 JCS canonicalization, SHA-256 hash-chain continuity, ECDSA-P256 / Ed25519 signature verification, dual-role human quorums, and Separation of Duties (SoD). |
| `odysseus/trust/store.py` | `AppendOnlyTrustStore`: cross-platform exclusively locked (`msvcrt` on Windows, `fcntl` on POSIX) append-only JSONL ledger with write-time validation, durable challenge-nonce replay protection, and tamper-evident snapshot queries. |
| `odysseus/trust/gate.py` | `ReleaseGateVerifier`: replays the full hash-chained log for a `certification_id`, verifies exact OPS-V3-202 subject bindings (artifact, SBOM, runtime baseline, nonce), enforces snapshot TTL (default `60s`) and clock-skew (`<= 5s`) bounds, and fails closed on any anomaly. |
| `odysseus/trust/cli.py` | Operator & CI/CD CLI (`python -m odysseus.trust.cli`) supporting `sign-entry`, `append`, `verify-ledger`, `inspect`, and `gate-check`. |

---

## Certification Lifecycle State Machine

Only **`CERTIFIED`** authorizes new releases. **`REVOKED`**, **`EXPIRED`**, and **`SUPERSEDED`** are strictly terminal states.

| From State | To State | Authorization Policy | Reversible? |
| :--- | :--- | :--- | :--- |
| `PENDING` | `CERTIFIED` | Automated Verifier / System (`1-OF-1-SYSTEM`) x Forward transitions only |
| `PENDING` | `EXPIRED` | Automated Timer | Terminal |
| `CERTIFIED` | `SUSPENDED` | Automated Policy Engine or Security Officer | Yes (via Human Quorum) |
| `CERTIFIED` | `REVOKED` | Human Quorum (`>= 1 SECURITY_AUTHORITY + >= 1 GOVERNANCE_AUTHORITY + WebAuthn`) | **Terminal** |
| `CERTIFIED` | `EXPIRED` | Automated Timer | **Terminal** |
| `CERTIFIED` | `SUPERSEDED` | Automated Policy Engine (requires `superseded_by_certification_id`) | **Terminal** |
| `SUSPENDED` | `CERTIFIED` | Human Quorum + **Separation of Duties** (original certifier key IDs blocked) | Yes |
| `SUSPENDED` | `REVOKED` | Human Quorum (`>= 1 SECURITY_AUTHORITY + >= 1 GOVERNANCE_AUTHORITY + WebAuthn`) | **Terminal** |
| `SUSPENDED` | `EXPIRED` | Automated Timer | **Terminal** |

---

## Python API Quickstart

```python
from pathlib import Path
from odysseus.trust import (AppendOnlyTrustStore, ReleaseGateVerifier, SubjectSpec, TrustRegistryValidator)
from odysseus.trust.cli import load_trusted_keys

trusted_keys = load_trusted_keys(Path("path/to/keyring.json"))
validator = TrustRegistryValidator(schema_path=Path("schemas/ops-v3-203/trust-registry-entry.v1.json"), trusted_keys=trusted_keys)
store = AppendOnlyTrustStore(ledger_path=Path("path/to/ledger.jsonl"), validator=validator)
gate = ReleaseGateVerifier(validator=validator, fetch_snapshot=store.get_snapshot, max_cache_ttl_seconds=60.0)

decision = gate.evaluate_release(SubjectSpec(certification_id="11111111-1111-4111-8111-111111111111", artifact_sha256="a"*64, sbom_sha256="b"*64, environment_baseline_sha256="c"*64, ops_v3_202_challenge_nonce="nonce-"+"d"*32))
assert decision.rollback_running_workloads is False
```

---

## CLI Reference

Exit codes: `0` = `ALLOW` / valid, `1` = `DENY` / fail-closed validation failure, `2` = configuration or I/O error.

```powershell
python -m odysseus.trust.cli --keys keyring.json --ledger ledger.jsonl sign-entry --certification-id 11111111-1111-4111-8111-111111111111 --from-state PENDING --to-state CERTIFIED --trigger-class VALIDATION_PASS --reason-code OPS_V3_202_PASS --evidence-uri https://odyai.internal/evidence/1.json --evidence-sha256 eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee --artifact-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --sbom-sha256 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --environment-baseline-sha256 cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc --challenge-nonce nonce-dddddddddddddddddddddddddddddddd --signer "key-policy-01|policy@odyai.internal|policy.priv.pem" --append
python -m odysseus.trust.cli --keys keyring.json --ledger ledger.jsonl verify-ledger
python -m odysseus.trust.cli --keys keyring.json --ledger ledger.jsonl inspect --certification-id 11111111-1111-4111-8111-111111111111
python -m odysseus.trust.cli --keys keyring.json --ledger ledger.jsonl gate-check --certification-id 11111111-1111-4111-8111-111111111111 --artifact-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --sbom-sha256 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --environment-baseline-sha256 cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc --challenge-nonce nonce-dddddddddddddddddddddddddddddddd
```

---

## Running Tests

```powershell
python -m pytest tests\test_trust_validator.py tests\test_trust_gate.py tests\test_trust_store.py tests\test_trust_cli.py -v --noconftest
```
