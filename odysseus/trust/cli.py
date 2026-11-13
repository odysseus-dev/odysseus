import argparse, base64, hashlib, json, sys, uuid, rfc8785
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from odysseus.trust.gate import ReleaseGateVerifier, SubjectSpec
from odysseus.trust.store import AppendOnlyTrustStore, TrustRegistryStoreError
from odysseus.trust.validator import TrustedKey, TrustRegistryValidator, TrustValidationError

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def load_trusted_keys(keys_path: Path) -> dict[str, TrustedKey]:
    raw = json.loads(keys_path.read_text(encoding='utf-8-sig'))
    items = raw['keys'] if isinstance(raw, dict) and 'keys' in raw else raw
    if not isinstance(items, list) or not items:
        raise ValueError('Keyring must contain a non-empty list of keys')
    keyring = {}
    for item in items:
        kid = item['key_id']
        if kid in keyring:
            raise ValueError('Duplicate key_id: ' + kid)
        pem = item['public_key_pem']
        keyring[kid] = TrustedKey(kid, item['role'], item['algorithm'], pem.encode('utf-8') if isinstance(pem, str) else bytes(pem))
    return keyring

def _build_store(args: argparse.Namespace) -> tuple[TrustRegistryValidator, AppendOnlyTrustStore]:
    val = TrustRegistryValidator(Path(args.schema), load_trusted_keys(Path(args.keys)))
    return val, AppendOnlyTrustStore(Path(args.ledger), val)

def _sign_bytes(km: TrustedKey, priv_path: Path, msg: bytes) -> str:
    pk = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    if km.algorithm == 'ED25519' and isinstance(pk, ed25519.Ed25519PrivateKey):
        return _b64url_encode(pk.sign(msg))
    if km.algorithm == 'ECDSA-P256-SHA256' and isinstance(pk, ec.EllipticCurvePrivateKey):
        return _b64url_encode(pk.sign(msg, ec.ECDSA(hashes.SHA256())))
    raise ValueError('Private key mismatch for ' + km.key_id)

def _parse_signer_spec(spec: str) -> tuple[str, str, Path, str | None]:
    parts = spec.split('|')
    if len(parts) not in (3, 4):
        raise ValueError('Invalid --signer spec: ' + spec)
    return parts[0].strip(), parts[1].strip(), Path(parts[2].strip()), parts[3].strip() if len(parts) == 4 and parts[3].strip() else None

def _cmd_sign_entry(args: argparse.Namespace) -> int:
    tkeys = load_trusted_keys(Path(args.keys))
    val = TrustRegistryValidator(Path(args.schema), tkeys)
    store = AppendOnlyTrustStore(Path(args.ledger), val)
    with store._exclusive_lock():
        existing = store._read_all_entries_unlocked()
    seq = existing[-1]['payload']['sequence_number'] + 1 if existing else 1
    prev_sha = hashlib.sha256(existing[-1]['canonical_payload_b64'].encode('ascii')).hexdigest() if existing else '0' * 64
    cert_entries = [e for e in existing if e['payload']['certification_id'] == args.certification_id]
    if cert_entries:
        fp = cert_entries[0]['payload']
        binding, orig = dict(fp['subject_binding']), list(fp['authorization']['original_certifier_ids'])
    else:
        if not all([args.artifact_sha256, args.sbom_sha256, args.environment_baseline_sha256, args.challenge_nonce]):
            raise ValueError('New certification_id requires subject binding flags')
        binding = {'artifact_sha256': args.artifact_sha256, 'sbom_sha256': args.sbom_sha256, 'environment_baseline_sha256': args.environment_baseline_sha256, 'ops_v3_202_challenge_nonce': args.challenge_nonce}
        orig = list(args.original_certifier_id or [])
    signers = [_parse_signer_spec(s) for s in args.signer]
    if not orig and args.to_state == 'CERTIFIED' and args.from_state == 'PENDING':
        orig = [kid for kid, _, _, _ in signers]
    trans = {'from_state': args.from_state, 'to_state': args.to_state, 'trigger_class': args.trigger_class, 'reason_code': args.reason_code, 'evidence_refs': [{'uri': args.evidence_uri, 'sha256': args.evidence_sha256, 'media_type': args.evidence_media_type}]}
    if args.superseded_by:
        trans['superseded_by_certification_id'] = args.superseded_by
    payload = {'entry_id': args.entry_id or str(uuid.uuid4()), 'sequence_number': seq, 'prev_entry_sha256': prev_sha, 'timestamp_utc': args.timestamp_utc or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'certification_id': args.certification_id, 'subject_binding': binding, 'transition': trans, 'authorization': {'mode': args.auth_mode, 'required_quorum': args.required_quorum, 'original_certifier_ids': orig}}
    canon = rfc8785.dumps(payload)
    sigs = []
    for kid, sid, ppath, wa in signers:
        km = tkeys.get(kid)
        if not km:
            raise TrustValidationError('Unknown signer key_id: ' + kid)
        sigs.append({'signer_id': sid, 'role': km.role, 'key_id': kid, 'algorithm': km.algorithm, 'webauthn_authenticator_data': wa, 'signature_b64': _sign_bytes(km, ppath, canon)})
    entry = {'envelope_version': '1.0.0', 'payload_type': 'application/vnd.odyai.trust-transition+json', 'canonical_payload_b64': _b64url_encode(canon), 'payload': payload, 'signatures': sigs}
    val.validate_entry(entry, previous_entry=existing[-1] if existing else None)
    if args.out:
        Path(args.out).write_text(json.dumps(entry, indent=2), encoding='utf-8')
    if args.append:
        store.append_entry(entry)
    print(json.dumps({'status': 'SIGNED_AND_APPENDED' if args.append else 'SIGNED', 'entry_id': payload['entry_id'], 'sequence_number': seq, 'certification_id': args.certification_id}, indent=2))
    return 0

def _cmd_verify_ledger(args: argparse.Namespace) -> int:
    val, store = _build_store(args)
    with store._exclusive_lock():
        entries = store._read_all_entries_unlocked()
    prev = None
    for e in entries:
        val.validate_entry(e, previous_entry=prev)
        prev = e
    print(json.dumps({'status': 'VALID', 'verified_entries': len(entries), 'head_sequence_number': prev['payload']['sequence_number'] if prev else 0}, indent=2))
    return 0

def _cmd_inspect(args: argparse.Namespace) -> int:
    _, store = _build_store(args)
    entries = store.get_entries_for_certification(args.certification_id)
    if not entries:
        print(json.dumps({'certification_id': args.certification_id, 'found': False, 'effective_state': 'UNKNOWN'}, indent=2))
        return 1
    print(json.dumps({'certification_id': args.certification_id, 'found': True, 'effective_state': entries[-1]['payload']['transition']['to_state'], 'transition_count': len(entries)}, indent=2))
    return 0

def _cmd_append(args: argparse.Namespace) -> int:
    _, store = _build_store(args)
    entry = json.loads(Path(args.entry).read_text(encoding='utf-8-sig'))
    store.append_entry(entry)
    print(json.dumps({'status': 'APPENDED', 'entry_id': entry['payload']['entry_id'], 'sequence_number': entry['payload']['sequence_number']}, indent=2))
    return 0

def _cmd_gate_check(args: argparse.Namespace) -> int:
    val, store = _build_store(args)
    gate = ReleaseGateVerifier(val, store.get_snapshot, max_cache_ttl_seconds=args.max_ttl_seconds, max_clock_skew_seconds=args.max_clock_skew_seconds)
    dec = gate.evaluate_release(SubjectSpec(args.certification_id, args.artifact_sha256, args.sbom_sha256, args.environment_baseline_sha256, args.challenge_nonce))
    print(json.dumps(asdict(dec), indent=2))
    return 0 if dec.allowed else 1

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='odysseus-trust')
    p.add_argument('--schema', default='schemas/ops-v3-203/trust-registry-entry.v1.json')
    p.add_argument('--keys', required=True)
    p.add_argument('--ledger', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    ps = sub.add_parser('sign-entry')
    ps.add_argument('--certification-id', required=True)
    ps.add_argument('--from-state', required=True)
    ps.add_argument('--to-state', required=True)
    ps.add_argument('--trigger-class', required=True)
    ps.add_argument('--reason-code', required=True)
    ps.add_argument('--evidence-uri', required=True)
    ps.add_argument('--evidence-sha256', required=True)
    ps.add_argument('--evidence-media-type', default='application/json')
    ps.add_argument('--auth-mode', default='AUTOMATED_POLICY_ENGINE')
    ps.add_argument('--required-quorum', default='1-OF-1-SYSTEM')
    ps.add_argument('--signer', action='append', required=True)
    ps.add_argument('--artifact-sha256')
    ps.add_argument('--sbom-sha256')
    ps.add_argument('--environment-baseline-sha256')
    ps.add_argument('--challenge-nonce')
    ps.add_argument('--original-certifier-id', action='append')
    ps.add_argument('--superseded-by')
    ps.add_argument('--entry-id')
    ps.add_argument('--timestamp-utc')
    ps.add_argument('--out')
    ps.add_argument('--append', action='store_true')
    ps.set_defaults(func=_cmd_sign_entry)
    pv = sub.add_parser('verify-ledger')
    pv.set_defaults(func=_cmd_verify_ledger)
    pi = sub.add_parser('inspect')
    pi.add_argument('--certification-id', required=True)
    pi.set_defaults(func=_cmd_inspect)
    pa = sub.add_parser('append')
    pa.add_argument('--entry', required=True)
    pa.set_defaults(func=_cmd_append)
    pg = sub.add_parser('gate-check')
    pg.add_argument('--certification-id', required=True)
    pg.add_argument('--artifact-sha256', required=True)
    pg.add_argument('--sbom-sha256', required=True)
    pg.add_argument('--environment-baseline-sha256', required=True)
    pg.add_argument('--challenge-nonce', required=True)
    pg.add_argument('--max-ttl-seconds', type=float, default=60.0)
    pg.add_argument('--max-clock-skew-seconds', type=float, default=5.0)
    pg.set_defaults(func=_cmd_gate_check)
    return p

def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TrustValidationError, TrustRegistryStoreError, ValueError) as exc:
        print(json.dumps({'status': 'FAIL_CLOSED', 'error': str(exc)}, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({'status': 'ERROR', 'error': str(exc)}, indent=2), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
