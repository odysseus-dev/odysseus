"""The managed browser policy must distrust exactly the incident's eight keys."""

import base64
import hashlib
import json
from pathlib import Path
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import serialization


ROOT = Path(__file__).resolve().parents[1]
# Public incident inventory: impacted-intermediates.json, 2026-10-21.
COMPROMISED_SPKIS = {
    "43bffa19d6ef651451f6841e019931e96f6f2eaeff28ab4b6f1ce4576de2717b",
    "49dd353dc2b919a3770645117d0e60c16115e7d1bc259eefe734b00ea434fe5f",
    "5c0660a2b164910d8ff589c18e14552e9e0ec237dcefe2cb736d41a4811f81a0",
    "63ecefee431d49523bc1136c4e72d0206a477086caf606cd5cf025686c109cf8",
    "92c063d67e8f14db71d288e11266ffdaf69cbf115f7bab14f3bc2f68d25e0e30",
    "e0254c7fcdc265e2f0f8a428dabb75ab4ae039b4872d62f5ee57f0877d5a8a41",
    "ebed6475c2c2d80fcb199f3fe8dd6e6b3ce60b77eb15d4e42c3dec0c6f5b5105",
    "ec4ffa1ad0ac88081314b7aaffef6a91f97cd57291bb5ba686c5415f5eb4f6b6",
}


class ChromiumCATrustTests(unittest.TestCase):
    def test_policy_distrusts_each_compromised_key_without_adding_trust(self):
        policy = json.loads((ROOT / "docker" / "chromium-emudhra.json").read_text())
        self.assertEqual(set(policy), {"CADistrustedCertificates"})
        self.assertEqual(len(policy["CADistrustedCertificates"]), 8)
        keys = set()
        for encoded in policy["CADistrustedCertificates"]:
            certificate = x509.load_der_x509_certificate(base64.b64decode(encoded, validate=True))
            self.assertTrue(certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
            self.assertNotEqual(certificate.subject, certificate.issuer)
            public_key = certificate.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            keys.add(hashlib.sha256(public_key).hexdigest())
        self.assertEqual(keys, COMPROMISED_SPKIS)
