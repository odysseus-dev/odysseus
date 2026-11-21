import base64, hashlib, json, uuid, rfc8785
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from jsonschema import Draft202012Validator, FormatChecker
from odysseus.trust.store import AppendOnlyTrustStore
from odysseus.trust.validator import TrustValidationError

class AttestationValidationError(Exception):
    pass

@dataclass(frozen=True)
class AttesterKey:
    key_id: str
    algorithm: str
    public_key_pem: bytes

@dataclass(frozen=True)
class PolicySignerConfig:
    key_id: str
    signer_id: str
    algorithm: str
    private_key_pem: bytes
    evidence_base_uri: str = 'https://odyai.internal/attestation/reports'

@dataclass(frozen=True)
class AttestationEvaluationResult:
    report_id: str
    certification_id: str
    status: str
    mismatched_fields: tuple[str, ...]
    suspension_triggered: bool
    trigger_class: str | None
    suspension_entry_id: str | None

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + '=' * ((4 - len(value) % 4) % 4)).encode('ascii'))

class RuntimeAttestationVerifier:
    def __init__(self, schema_path: Path, attester_keys: dict[str, AttesterKey], store: AppendOnlyTrustStore, policy_signer: PolicySignerConfig, *, clock: Callable[[], datetime] | None = None) -> None:
        self._schema_validator = Draft202012Validator(json.loads(schema_path.read_text(encoding='utf-8-sig')), format_checker=FormatChecker())
        self._attester_keys = dict(attester_keys)
        self._store = store
        self._policy_signer = policy_signer
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._seen_report_ids: set[str] = set()
        self._consumed_epoch_nonces: set[str] = set()

    def _verify_signature(self, envelope: dict[str, Any], canonical_bytes: bytes) -> None:
        sig = envelope['signature']
        kid = sig['key_id']
        km = self._attester_keys.get(kid)
        if not km or km.algorithm != sig['algorithm']:
            raise AttestationValidationError('Untrusted or mismatched attester key: ' + kid)
        raw_sig = _b64url_decode(sig['signature_b64'])
        pub = serialization.load_pem_public_key(km.public_key_pem)
        try:
            if km.algorithm == 'ED25519' and isinstance(pub, ed25519.Ed25519PublicKey):
                pub.verify(raw_sig, canonical_bytes)
            elif km.algorithm == 'ECDSA-P256-SHA256' and isinstance(pub, ec.EllipticCurvePublicKey):
                pub.verify(raw_sig, canonical_bytes, ec.ECDSA(hashes.SHA256()))
            else:
                raise AttestationValidationError('Unsupported attester algorithm')
        except Exception as exc:
            raise AttestationValidationError('Invalid attestation signature from ' + kid) from exc

    def _sign_policy_bytes(self, canonical_bytes: bytes) -> str:
        priv = serialization.load_pem_private_key(self._policy_signer.private_key_pem, password=None)
        if self._policy_signer.algorithm == 'ED25519' and isinstance(priv, ed25519.Ed25519PrivateKey):
            return _b64url_encode(priv.sign(canonical_bytes))
        if self._policy_signer.algorithm == 'ECDSA-P256-SHA256' and isinstance(priv, ec.EllipticCurvePrivateKey):
            return _b64url_encode(priv.sign(canonical_bytes, ec.ECDSA(hashes.SHA256())))
        raise ValueError('Policy signer key mismatch')

    def _trigger_registry_suspension(self, *, certification_id: str, trigger_class: str, reason_code: str, report_id: str, report_sha256: str) -> str | None:
        entries = self._store.get_entries_for_certification(certification_id)
        if not entries:
            raise TrustValidationError('Unknown certification_id: ' + certification_id)
        if entries[-1]['payload']['transition']['to_state'] != 'CERTIFIED':
            return None
        with self._store._exclusive_lock():
            all_entries = self._store._read_all_entries_unlocked()
        tail = all_entries[-1]
        genesis = entries[0]['payload']
        eid = str(uuid.uuid4())
        ev_uri = self._policy_signer.evidence_base_uri.rstrip('/') + '/' + report_id + '.json'
        payload = {
            'entry_id': eid,
            'sequence_number': tail['payload']['sequence_number'] + 1,
            'prev_entry_sha256': hashlib.sha256(tail['canonical_payload_b64'].encode('ascii')).hexdigest(),
            'timestamp_utc': self._clock().astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'certification_id': certification_id,
            'subject_binding': dict(genesis['subject_binding']),
            'transition': {'from_state': 'CERTIFIED', 'to_state': 'SUSPENDED', 'trigger_class': trigger_class, 'reason_code': reason_code, 'evidence_refs': [{'uri': ev_uri, 'sha256': report_sha256, 'media_type': 'application/vnd.odyai.runtime-attestation+json'}]},
            'authorization': {'mode': 'AUTOMATED_POLICY_ENGINE', 'required_quorum': '1-OF-1-SYSTEM', 'original_certifier_ids': list(genesis['authorization']['original_certifier_ids'])},
        }
        canon = rfc8785.dumps(payload)
        self._store.append_entry({'envelope_version': '1.0.0', 'payload_type': 'application/vnd.odyai.trust-transition+json', 'canonical_payload_b64': _b64url_encode(canon), 'payload': payload, 'signatures': [{'signer_id': self._policy_signer.signer_id, 'role': 'POLICY_ENGINE', 'key_id': self._policy_signer.key_id, 'algorithm': self._policy_signer.algorithm, 'webauthn_authenticator_data': None, 'signature_b64': self._sign_policy_bytes(canon)}]})
        return eid

    def process_report(self, envelope: dict[str, Any], *, expected_epoch_nonce: str) -> AttestationEvaluationResult:
        errs = sorted(self._schema_validator.iter_errors(envelope), key=lambda e: list(e.path))
        if errs:
            raise AttestationValidationError('Schema error: ' + errs[0].message)
        payload = envelope['payload']
        canon = rfc8785.dumps(payload)
        if _b64url_encode(canon) != envelope['canonical_payload_b64']:
            raise AttestationValidationError('canonical_payload_b64 mismatch')
        self._verify_signature(envelope, canon)
        rid, cid, enonce = payload['report_id'], payload['certification_id'], payload['epoch_attestation']['epoch_nonce']
        if rid in self._seen_report_ids:
            raise AttestationValidationError('Duplicate report_id: ' + rid)
        self._seen_report_ids.add(rid)
        entries = self._store.get_entries_for_certification(cid)
        if not entries:
            raise AttestationValidationError('Unknown certification_id: ' + cid)
        lb, obs = entries[0]['payload']['subject_binding'], payload['observed_measurements']
        mismatches, tamper = [], False
        if enonce != expected_epoch_nonce or enonce in self._consumed_epoch_nonces:
            mismatches.append('epoch_nonce')
            tamper = True
        else:
            self._consumed_epoch_nonces.add(enonce)
        if payload['certified_binding'] != lb:
            tamper = True
        for f in ('artifact_sha256', 'sbom_sha256', 'environment_baseline_sha256'):
            if obs[f] != lb[f]:
                mismatches.append(f)
        rhash = hashlib.sha256(envelope['canonical_payload_b64'].encode('ascii')).hexdigest()
        if tamper:
            eid = self._trigger_registry_suspension(certification_id=cid, trigger_class='TAMPER_OR_REPLAY', reason_code='OPS_V3_204_TAMPER_OR_REPLAY', report_id=rid, report_sha256=rhash)
            return AttestationEvaluationResult(rid, cid, 'TAMPER_DETECTED', tuple(mismatches), eid is not None, 'TAMPER_OR_REPLAY', eid)
        if mismatches:
            eid = self._trigger_registry_suspension(certification_id=cid, trigger_class='ENVIRONMENT_DRIFT', reason_code='OPS_V3_204_RUNTIME_DRIFT', report_id=rid, report_sha256=rhash)
            return AttestationEvaluationResult(rid, cid, 'DRIFT_DETECTED', tuple(mismatches), eid is not None, 'ENVIRONMENT_DRIFT', eid)
        return AttestationEvaluationResult(rid, cid, 'HEALTHY', (), False, None, None)
