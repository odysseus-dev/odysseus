import hashlib, json, logging, os, uuid
from dataclasses import asdict
from pathlib import Path
from fastapi import APIRouter, HTTPException
from odysseus.trust.attestation import AttesterKey, PolicySignerConfig, RuntimeAttestationVerifier
from odysseus.trust.cli import load_trusted_keys
from odysseus.trust.gate import GateDecision, ReleaseGateVerifier, SubjectSpec
from odysseus.trust.store import AppendOnlyTrustStore
from odysseus.trust.validator import TrustRegistryValidator

logger = logging.getLogger(__name__)
TRUST_DIR = Path('data/trust')
SCHEMA_203 = Path('schemas/ops-v3-203/trust-registry-entry.v1.json')
SCHEMA_204 = Path('schemas/ops-v3-204/runtime-attestation.v1.json')

def evaluate_current_trust_gate() -> GateDecision:
    manifest_path = TRUST_DIR / 'current_cert.json'
    keyring_path = TRUST_DIR / 'keyring.json'
    ledger_path = TRUST_DIR / 'ledger.jsonl'
    if not (manifest_path.exists() and keyring_path.exists() and ledger_path.exists() and SCHEMA_203.exists()):
        return GateDecision(False, 'DENY', 'UNINITIALIZED', 'Fail-closed: trust registry files missing', 'unknown', None, False)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    val = TrustRegistryValidator(SCHEMA_203, load_trusted_keys(keyring_path))
    store = AppendOnlyTrustStore(ledger_path, val)
    gate = ReleaseGateVerifier(val, store.get_snapshot)
    live_art = hashlib.sha256(Path('app.py').read_bytes()).hexdigest()
    live_sbom = hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest()
    live_env = hashlib.sha256(Path('pyproject.toml').read_bytes()).hexdigest()
    return gate.evaluate_release(SubjectSpec(manifest['certification_id'], live_art, live_sbom, live_env, manifest['challenge_nonce']))

def setup_trust_routes() -> APIRouter:
    router = APIRouter(prefix='/api/trust', tags=['trust'])

    @router.get('/status')
    async def get_trust_status():
        dec = evaluate_current_trust_gate()
        return {'gate_a_go': os.getenv('TRUST_GATE_A_GO', '0') == '1', 'decision': asdict(dec)}

    @router.post('/heartbeat')
    async def run_runtime_heartbeat():
        manifest_path = TRUST_DIR / 'current_cert.json'
        keyring_path = TRUST_DIR / 'keyring.json'
        ledger_path = TRUST_DIR / 'ledger.jsonl'
        priv_path = TRUST_DIR / 'keys/key-policy-01.priv.pem'
        if not (manifest_path.exists() and keyring_path.exists() and ledger_path.exists() and priv_path.exists()):
            raise HTTPException(status_code=503, detail='Trust registry not bootstrapped')
        import base64, rfc8785
        from datetime import datetime, timezone
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        val = TrustRegistryValidator(SCHEMA_203, load_trusted_keys(keyring_path))
        store = AppendOnlyTrustStore(ledger_path, val)
        att_priv = ed25519.Ed25519PrivateKey.generate()
        att_pub = att_priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        verifier = RuntimeAttestationVerifier(SCHEMA_204, {'local-runtime-01': AttesterKey('local-runtime-01', 'ED25519', att_pub)}, store, PolicySignerConfig('key-policy-01', 'policy@odyai.internal', 'ED25519', priv_path.read_bytes()))
        nonce = 'hb-' + uuid.uuid4().hex
        obs_art = hashlib.sha256(Path('app.py').read_bytes()).hexdigest()
        obs_sbom = hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest()
        obs_env = hashlib.sha256(Path('pyproject.toml').read_bytes()).hexdigest()
        mismatches = [k for k, obs, cert in [('artifact_sha256', obs_art, manifest['artifact_sha256']), ('sbom_sha256', obs_sbom, manifest['sbom_sha256']), ('environment_baseline_sha256', obs_env, manifest['environment_baseline_sha256'])] if obs != cert]
        status = 'DRIFT_DETECTED' if mismatches else 'HEALTHY'
        payload = {'report_id': str(uuid.uuid4()), 'timestamp_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'certification_id': manifest['certification_id'], 'workload_identity': {'spiffe_id': 'spiffe://odyai.internal/local/odysseus', 'node_id': 'local-host', 'enclave_type': 'TPM_2_0', 'hardware_quote_sha256': '0' * 64}, 'epoch_attestation': {'epoch_sequence': 1, 'epoch_nonce': nonce, 'interval_seconds': 60}, 'certified_binding': {'artifact_sha256': manifest['artifact_sha256'], 'sbom_sha256': manifest['sbom_sha256'], 'environment_baseline_sha256': manifest['environment_baseline_sha256'], 'ops_v3_202_challenge_nonce': manifest['challenge_nonce']}, 'observed_measurements': {'artifact_sha256': obs_art, 'sbom_sha256': obs_sbom, 'environment_baseline_sha256': obs_env}, 'evaluation': {'status': status, 'mismatched_fields': mismatches, 'recommended_trigger_class': 'ENVIRONMENT_DRIFT' if mismatches else None}}
        canon = rfc8785.dumps(payload)
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')
        env = {'envelope_version': '1.0.0', 'payload_type': 'application/vnd.odyai.runtime-attestation+json', 'canonical_payload_b64': b64(canon), 'payload': payload, 'signature': {'attester_id': 'local-runtime', 'key_id': 'local-runtime-01', 'algorithm': 'ED25519', 'signature_b64': b64(att_priv.sign(canon))}}
        res = verifier.process_report(env, expected_epoch_nonce=nonce)
        return asdict(res)

    return router
