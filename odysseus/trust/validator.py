import base64, hashlib, json, rfc8785
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from jsonschema import Draft202012Validator, FormatChecker

class TrustValidationError(Exception):
    pass

@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    role: str
    algorithm: str
    public_key_pem: bytes

ALLOWED_TRANSITIONS = {
    'PENDING': {'CERTIFIED', 'EXPIRED'},
    'CERTIFIED': {'SUSPENDED', 'REVOKED', 'EXPIRED', 'SUPERSEDED'},
    'SUSPENDED': {'CERTIFIED', 'REVOKED', 'EXPIRED'},
    'REVOKED': set(), 'EXPIRED': set(), 'SUPERSEDED': set(),
}

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + '=' * ((4 - len(value) % 4) % 4)).encode('ascii'))

class TrustRegistryValidator:
    def __init__(self, schema_path: Path, trusted_keys: dict[str, TrustedKey]) -> None:
        self._schema_validator = Draft202012Validator(json.loads(schema_path.read_text(encoding='utf-8-sig')), format_checker=FormatChecker())
        self._trusted_keys = dict(trusted_keys)

    def _verify_sig(self, sig_obj: dict[str, Any], canonical_bytes: bytes) -> TrustedKey:
        kid = sig_obj['key_id']
        km = self._trusted_keys.get(kid)
        if not km or km.role != sig_obj['role'] or km.algorithm != sig_obj['algorithm']:
            raise TrustValidationError(f'Invalid or untrusted signer metadata: {kid}')
        raw_sig = _b64url_decode(sig_obj['signature_b64'])
        pub = serialization.load_pem_public_key(km.public_key_pem)
        try:
            if km.algorithm == 'ED25519' and isinstance(pub, ed25519.Ed25519PublicKey):
                pub.verify(raw_sig, canonical_bytes)
            elif km.algorithm == 'ECDSA-P256-SHA256' and isinstance(pub, ec.EllipticCurvePublicKey):
                pub.verify(raw_sig, canonical_bytes, ec.ECDSA(hashes.SHA256()))
            else:
                raise TrustValidationError(f'Unsupported algorithm for {kid}')
        except Exception as exc:
            raise TrustValidationError(f'Invalid signature from {kid}') from exc
        return km

    def validate_entry(self, entry: dict[str, Any], *, previous_entry: dict[str, Any] | None = None) -> None:
        errs = sorted(self._schema_validator.iter_errors(entry), key=lambda e: list(e.path))
        if errs:
            raise TrustValidationError(f'Schema validation failed: {errs[0].message}')
        payload = entry['payload']
        canon = rfc8785.dumps(payload)
        if _b64url_encode(canon) != entry['canonical_payload_b64']:
            raise TrustValidationError('canonical_payload_b64 does not match RFC 8785 JCS bytes')
        seq, prev_hash = payload['sequence_number'], payload['prev_entry_sha256']
        if previous_entry is None:
            if seq != 1 or prev_hash != '0' * 64:
                raise TrustValidationError('Genesis entry requires seq=1 and zeroed prev_entry_sha256')
        else:
            pp = previous_entry['payload']
            if seq != pp['sequence_number'] + 1:
                raise TrustValidationError('Non-contiguous sequence_number')
            if prev_hash != hashlib.sha256(previous_entry['canonical_payload_b64'].encode('ascii')).hexdigest():
                raise TrustValidationError('prev_entry_sha256 hash chain mismatch')
        trans = payload['transition']
        fs, ts = trans['from_state'], trans['to_state']
        if ts not in ALLOWED_TRANSITIONS.get(fs, set()):
            raise TrustValidationError(f'Illegal transition: {fs} -> {ts}')
        if previous_entry and previous_entry['payload']['certification_id'] == payload['certification_id']:
            if fs != previous_entry['payload']['transition']['to_state']:
                raise TrustValidationError('State continuity broken')
        if ts == 'SUPERSEDED' and not trans.get('superseded_by_certification_id'):
            raise TrustValidationError('SUPERSEDED requires superseded_by_certification_id')
        vkeys, seen = [], set()
        for s in entry['signatures']:
            if s['key_id'] in seen:
                raise TrustValidationError('Duplicate signer key_id')
            seen.add(s['key_id'])
            vkeys.append(self._verify_sig(s, canon))
        auth = payload['authorization']
        mode, roles = auth['mode'], {k.role for k in vkeys}
        if (ts == 'REVOKED' or (fs == 'SUSPENDED' and ts == 'CERTIFIED')) and mode not in ('HUMAN_QUORUM', 'DISPUTE_REVIEW_BOARD'):
            raise TrustValidationError('Human quorum required')
        if mode == 'HUMAN_QUORUM':
            if len(vkeys) < 2 or 'SECURITY_AUTHORITY' not in roles or 'GOVERNANCE_AUTHORITY' not in roles:
                raise TrustValidationError('Dual-role human quorum required')
            if any(not s.get('webauthn_authenticator_data') for s in entry['signatures']):
                raise TrustValidationError('WebAuthn assertion required')
        if fs == 'SUSPENDED' and ts == 'CERTIFIED' and seen.intersection(auth.get('original_certifier_ids', [])):
            raise TrustValidationError('Separation of Duties violation')
