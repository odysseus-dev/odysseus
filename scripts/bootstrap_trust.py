import hashlib, json, uuid
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from odysseus.trust.cli import main

def bootstrap(base_dir: Path = Path('data/trust')) -> dict:
    keys_dir = base_dir / 'keys'
    keys_dir.mkdir(parents=True, exist_ok=True)
    keyring_path = base_dir / 'keyring.json'
    ledger_path = base_dir / 'ledger.jsonl'
    manifest_path = base_dir / 'current_cert.json'

    specs = [
        ('key-policy-01', 'POLICY_ENGINE', 'ED25519', ed25519.Ed25519PrivateKey.generate()),
        ('key-sec-01', 'SECURITY_AUTHORITY', 'ECDSA-P256-SHA256', ec.generate_private_key(ec.SECP256R1())),
        ('key-gov-01', 'GOVERNANCE_AUTHORITY', 'ECDSA-P256-SHA256', ec.generate_private_key(ec.SECP256R1())),
    ]
    pub_entries = []
    for kid, role, alg, priv in specs:
        priv_file = keys_dir / (kid + '.priv.pem')
        priv_file.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        pub_pem = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('utf-8')
        pub_entries.append({'key_id': kid, 'role': role, 'algorithm': alg, 'public_key_pem': pub_pem})

    keyring_path.write_text(json.dumps({'keys': pub_entries}, indent=2), encoding='utf-8')
    if ledger_path.exists():
        ledger_path.unlink()

    cert_id = str(uuid.uuid4())
    art_sha = hashlib.sha256(Path('app.py').read_bytes()).hexdigest()
    sbom_sha = hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest()
    env_sha = hashlib.sha256(Path('pyproject.toml').read_bytes()).hexdigest()
    nonce = 'bootstrap-nonce-' + uuid.uuid4().hex

    rc = main([
        '--keys', str(keyring_path), '--ledger', str(ledger_path), 'sign-entry',
        '--certification-id', cert_id, '--from-state', 'PENDING', '--to-state', 'CERTIFIED',
        '--trigger-class', 'VALIDATION_PASS', '--reason-code', 'LOCAL_BOOTSTRAP_CERT',
        '--evidence-uri', 'file://data/trust/bootstrap_evidence.json', '--evidence-sha256', '0' * 64,
        '--artifact-sha256', art_sha, '--sbom-sha256', sbom_sha,
        '--environment-baseline-sha256', env_sha, '--challenge-nonce', nonce,
        '--signer', 'key-policy-01|policy@odyai.internal|' + str(keys_dir / 'key-policy-01.priv.pem'),
        '--append',
    ])
    assert rc == 0, 'Bootstrap sign-entry failed'
    info = {'certification_id': cert_id, 'artifact_sha256': art_sha, 'sbom_sha256': sbom_sha, 'environment_baseline_sha256': env_sha, 'challenge_nonce': nonce}
    manifest_path.write_text(json.dumps(info, indent=2), encoding='utf-8')
    return info

if __name__ == '__main__':
    res = bootstrap()
    print('BOOTSTRAP COMPLETE:', json.dumps(res, indent=2))
