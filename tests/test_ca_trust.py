"""Offline regressions for incident roots, unaffected roots, and CA directories."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_ca_trust", ROOT / "scripts/check_ca_trust.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
INCIDENT = json.loads((ROOT / "tests/fixtures/ca-trust/incident.json").read_text())


class CATrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Public certificate data is embedded below. Tests create temporary
        # PEM files from these constants; no fixture contains a private key.
        cls.temporary = tempfile.TemporaryDirectory()
        cls.fixtures = Path(cls.temporary.name)
        for name, pem in PUBLIC_CERTIFICATES.items():
            (cls.fixtures / name).write_text(pem)
        for fingerprint, pem in INCIDENT["certificates"].items():
            if hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() != fingerprint:
                raise ValueError("incident fixture fingerprint mismatch")
            (cls.fixtures / f"{fingerprint}.pem").write_text(pem)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_unaffected_emsign_root_remains_usable(self):
        checker.check_bundle(self.fixtures / "emsign-g1.pem")

    def test_verbose_python_check_reports_and_rejects_affected_user_bundle(self):
        # A fresh interpreter must see persisted user packages, independently
        # of pytest's imported certifi/pip modules and any active test venv.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = dict(os.environ, HOME=str(home), PYTHONUSERBASE=str(home / ".local"))
            env.pop("PYTHONNOUSERSITE", None)
            env.pop("PYTHONPATH", None)
            python = getattr(sys, "_base_executable", sys.executable)
            site_result = subprocess.run(
                [python, "-m", "site", "--user-site"], env=env, capture_output=True, text=True,
            )
            self.assertEqual(site_result.returncode, 0, site_result.stderr)
            user_site = Path(site_result.stdout.strip())
            for package in ("certifi", "pip", "pip/_vendor", "pip/_vendor/certifi"):
                directory = user_site / package
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "__init__.py").write_text(
                    "from pathlib import Path\ndef where(): return str(Path(__file__).with_name('cacert.pem'))\n"
                )
            application_bundle = user_site / "certifi/cacert.pem"
            pip_bundle = user_site / "pip/_vendor/certifi/cacert.pem"
            for bundle in (application_bundle, pip_bundle):
                bundle.write_text(PUBLIC_CERTIFICATES["emsign-g1.pem"])
            command = [python, str(ROOT / "scripts/check_ca_trust.py"), "--python", "--verbose"]
            clean = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertIn(str(application_bundle), clean.stdout)
            self.assertIn(str(pip_bundle), clean.stdout)
            self.assertIn(str(home), clean.stdout)
            self.assertIn("Python executable:", clean.stdout)
            application_bundle.write_text(PUBLIC_CERTIFICATES["emsign-g3.pem"])
            affected = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(affected.returncode, 1, affected.stderr)
            self.assertIn(str(application_bundle), affected.stderr)
            self.assertIn("emSign ECC Root CA - G3", affected.stderr)

    def test_compromised_emsign_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "emSign ECC Root CA - G3"):
            checker.check_bundle(self.fixtures / "emsign-g3.pem")

    def test_aaa_cross_sign_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "AAA Certificate Services"):
            checker.check_bundle(self.fixtures / "aaa.pem")

    def test_mixed_bundle_does_not_hide_an_affected_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "mixed.pem"
            bundle.write_bytes((self.fixtures / "emsign-g1.pem").read_bytes() + (self.fixtures / "aaa.pem").read_bytes())
            with self.assertRaisesRegex(ValueError, "AAA Certificate Services"):
                checker.check_bundle(bundle)

    def test_all_published_intermediates_and_cross_signs_are_rejected(self):
        self.assertEqual(len(INCIDENT["intermediates"]), 16)
        for fingerprint, name in INCIDENT["intermediates"].items():
            with self.subTest(certificate=name), self.assertRaisesRegex(ValueError, "affected CA certificates"):
                checker.check_bundle(self.fixtures / f"{fingerprint}.pem")

    def test_published_roots_and_cross_signed_issuer_are_rejected(self):
        certificates = INCIDENT["roots"] | INCIDENT["cross_signed_roots"]
        self.assertEqual(len(certificates), 5)
        for fingerprint, name in certificates.items():
            with self.subTest(certificate=name), self.assertRaises(ValueError):
                checker.check_bundle(self.fixtures / f"{fingerprint}.pem")

    def test_clean_roots_do_not_hide_an_added_compromised_intermediate(self):
        fingerprint = next(iter(INCIDENT["intermediates"]))
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "custom.pem"
            bundle.write_text(PUBLIC_CERTIFICATES["emsign-g1.pem"] + INCIDENT["certificates"][fingerprint])
            with self.assertRaisesRegex(ValueError, "EM DV TLS CA - G3B"):
                checker.check_bundle(bundle)

    @unittest.skipUnless(shutil.which("openssl"), "offline chain verification requires OpenSSL")
    def test_published_chains_and_custom_anchor_with_affected_positive_control(self):
        # Freeze time inside the public fixtures' validity interval. Otherwise
        # expiry could make an ineffective trust-store fix appear successful.
        help_result = subprocess.run(["openssl", "verify", "-help"], capture_output=True, text=True)
        no_store = ["-no-CAstore"] if "-no-CAstore" in help_result.stdout + help_result.stderr else []
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            affected = directory / "affected.pem"
            affected.write_text("".join(INCIDENT["certificates"][key] for key in INCIDENT["roots"]))
            clean = self.fixtures / "emsign-g1.pem"
            custom = directory / "custom.pem"
            fingerprint = next(iter(INCIDENT["intermediates"]))
            custom.write_text(clean.read_text() + INCIDENT["certificates"][fingerprint])
            self.assertEqual(len(INCIDENT["chains"]), 14)
            custom_accepted = []
            for hostname, chain in INCIDENT["chains"].items():
                leaf, intermediates = directory / "leaf.pem", directory / "chain.pem"
                leaf.write_text(INCIDENT["certificates"][chain[0]])
                intermediates.write_text("".join(INCIDENT["certificates"][key] for key in chain[1:]))

                def verify(bundle):
                    return subprocess.run([
                        "openssl", "verify", "-no-CApath", *no_store,
                        "-attime", str(INCIDENT["verification_time"]),
                        "-partial_chain", "-x509_strict", "-purpose", "sslserver",
                        "-verify_hostname", hostname, "-CAfile", str(bundle),
                        "-untrusted", str(intermediates), str(leaf),
                    ], capture_output=True, text=True)

                with self.subTest(hostname=hostname):
                    positive = verify(affected)
                    self.assertEqual(positive.returncode, 0, positive.stderr)
                    negative = verify(clean)
                    self.assertNotEqual(negative.returncode, 0)
                    self.assertIn("unable to get local issuer certificate", negative.stderr)
                    if verify(custom).returncode == 0:
                        custom_accepted.append(hostname)
            self.assertCountEqual(custom_accepted, [
                "dv-g3b-aaa-emudhra.we-have-a-problem.com",
                "dv-g3b-aaa-noct-emudhra.we-have-a-problem.com",
                "dv-g3b-emudhra.we-have-a-problem.com",
                "dv-g3b-noct-emudhra.we-have-a-problem.com",
            ])
            checker.check_bundle(clean)
            for bundle in (affected, custom):
                with self.subTest(bundle=bundle.name), self.assertRaises(ValueError):
                    checker.check_bundle(bundle)

    def test_empty_malformed_and_missing_bundles_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for content in ("", "not a certificate"):
                path = Path(tmp) / "bad.pem"
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises((ssl.SSLError, ValueError)):
                    checker.check_bundle(path)
            with self.assertRaises(OSError):
                checker.check_bundle(Path(tmp) / "missing.pem")

    def test_each_configured_override_is_checked(self):
        fingerprint = next(iter(INCIDENT["intermediates"]))
        affected = self.fixtures / f"{fingerprint}.pem"
        clean = self.fixtures / "emsign-g1.pem"
        for name in ("LLM_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                     "PIP_CERT", "NODE_EXTRA_CA_CERTS", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"):
            for bundle, expected in ((clean, 0), (affected, 1)):
                with self.subTest(variable=name, bundle=bundle.name), patch.dict(
                    os.environ, {name: str(bundle)}, clear=True
                ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(checker.main(["--overrides", str(clean)]), expected)

    def test_invalid_override_is_not_hidden_by_a_clean_explicit_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, content in (("empty.pem", ""), ("malformed.pem", "invalid")):
                (directory / name).write_text(content)
            for name in ("missing.pem", "empty.pem", "malformed.pem"):
                with self.subTest(file=name), patch.dict(
                    os.environ, {"LLM_CA_BUNDLE": str(directory / name)}, clear=True
                ), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(checker.main(["--overrides", str(self.fixtures / "emsign-g1.pem")]), 1)

    def test_no_configured_overrides_does_not_report_validated_stores(self):
        with patch.dict(os.environ, {"LLM_CA_BUNDLE": "  "}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checker.main(["--overrides"]), 1)

    def test_requests_directory_override_checks_hashed_intermediates(self):
        fingerprint = next(iter(INCIDENT["intermediates"]))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT"):
                with self.subTest(variable=name), patch.dict(os.environ, {name: str(directory)}, clear=True):
                    for content, expected in ((PUBLIC_CERTIFICATES["emsign-g1.pem"], 0),
                                              (INCIDENT["certificates"][fingerprint], 1)):
                        (directory / "12345678.0").write_text(content)
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(checker.main(["--overrides"]), expected)
                    (directory / "12345678.0").unlink()
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(checker.main(["--overrides"]), 1)

    def test_safe_cafile_does_not_hide_stale_hashed_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "14bc7599.0").symlink_to(self.fixtures / "emsign-g3.pem")
            defaults = ssl.get_default_verify_paths()._replace(
                cafile=str(self.fixtures / "emsign-g1.pem"), capath=str(directory)
            )
            errors = io.StringIO()
            with patch.object(checker.ssl, "get_default_verify_paths", return_value=defaults), patch.dict(
                os.environ, {defaults.openssl_capath_env: str(directory)}
            ):
                with contextlib.redirect_stderr(errors):
                    result = checker.main(["--system"])
            self.assertEqual(result, 1)
            self.assertIn("emSign ECC Root CA - G3", errors.getvalue())

    def test_hashed_non_ca_certificate_does_not_reject_clean_store(self):
        # Ubuntu's OpenSSL directory can also hash ssl-cert-snakeoil.pem.
        # A valid server certificate has no CA entries in get_ca_certs().
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "ce275665.0").symlink_to(self.fixtures / "localhost-leaf.pem")
            defaults = ssl.get_default_verify_paths()._replace(
                cafile=str(self.fixtures / "emsign-g1.pem"), capath=str(directory)
            )
            with patch.object(checker.ssl, "get_default_verify_paths", return_value=defaults), patch.dict(
                os.environ, {defaults.openssl_capath_env: str(directory)}
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(checker.main(["--system"]), 0)

    def test_multiple_ca_directories_are_all_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean, stale = Path(tmp) / "clean", Path(tmp) / "stale"
            clean.mkdir()
            stale.mkdir()
            (stale / "ee64a828.0").symlink_to(self.fixtures / "aaa.pem")
            defaults = ssl.get_default_verify_paths()._replace(
                cafile=str(self.fixtures / "emsign-g1.pem"), capath=None
            )
            errors = io.StringIO()
            with patch.object(checker.ssl, "get_default_verify_paths", return_value=defaults), patch.dict(
                os.environ, {defaults.openssl_capath_env: os.pathsep.join(map(str, (clean, stale)))}
            ):
                with contextlib.redirect_stderr(errors):
                    result = checker.main(["--system"])
            self.assertEqual(result, 1)
            self.assertIn("AAA Certificate Services", errors.getvalue())

    def test_grpc_bundled_roots_are_checked_without_an_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "grpc"
            bundle = package / "_cython" / "_credentials" / "roots.pem"
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes((self.fixtures / "emsign-g3.pem").read_bytes())
            errors = io.StringIO()
            with patch.dict(os.environ, {"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": ""}), patch.object(
                checker.importlib.util, "find_spec", return_value=SimpleNamespace(origin=str(package / "__init__.py"))
            ), contextlib.redirect_stderr(errors):
                self.assertEqual(checker.main(["--grpc"]), 1)
            self.assertIn("emSign ECC Root CA - G3", errors.getvalue())

    def test_grpc_explicit_clean_store_replaces_the_independent_bundle(self):
        with patch.dict(os.environ, {"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": str(self.fixtures / "emsign-g1.pem")}), patch.object(
            checker.importlib.util, "find_spec", side_effect=AssertionError("must use the explicit store")
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(["--grpc"]), 0)

    def test_grpc_invalid_override_does_not_fall_back_silently(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name, content in (("empty.pem", ""), ("malformed.pem", "not a certificate"), ("affected.pem", PUBLIC_CERTIFICATES["aaa.pem"])):
                bundle = Path(temporary) / name
                bundle.write_text(content)
                with self.subTest(name=name), patch.dict(os.environ, {"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": str(bundle)}), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(checker.main(["--grpc"]), 1)
            with patch.dict(os.environ, {"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": str(Path(temporary) / "missing.pem")}), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(checker.main(["--grpc"]), 1)

    def test_grpc_check_reports_a_missing_installation(self):
        with patch.dict(os.environ, {"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": ""}), patch.object(
            checker.importlib.util, "find_spec", return_value=None
        ), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checker.main(["--grpc"]), 1)


# Public Mozilla CA certificates; provenance: tests/fixtures/ca-trust/README.md.
PUBLIC_CERTIFICATES = {
    'localhost-leaf.pem': """-----BEGIN CERTIFICATE-----
MIIBOTCB7KADAgECAhRcVxnOQNEFWf92K4dm4I8ChyjVMzAFBgMrZXAwFDESMBAG
A1UEAwwJbG9jYWxob3N0MB4XDTI2MTAyMTE4NTgwN1oXDTM2MTAxODE4NTgwN1ow
FDESMBAGA1UEAwwJbG9jYWxob3N0MCowBQYDK2VwAyEARglphvNgp9qRCESLHnuu
W8VC5urdZl+YgS+Ux1BoLryjUDBOMB0GA1UdDgQWBBSaEckyR/76lX3a5Ijh8B42
oJwN1DAfBgNVHSMEGDAWgBSaEckyR/76lX3a5Ijh8B42oJwN1DAMBgNVHRMBAf8E
AjAAMAUGAytlcANBAJkq9ayy+d4AUZnjhWM31rFke5DT+l9QFcPl34+dpVhqJV/L
m4IpPHYagqrBRHn9Dv/C8qYyPBKnAwJRpNptsgw=
-----END CERTIFICATE-----
""",
    'aaa.pem': """-----BEGIN CERTIFICATE-----
MIIEMjCCAxqgAwIBAgIBATANBgkqhkiG9w0BAQUFADB7MQswCQYDVQQGEwJHQjEb
MBkGA1UECAwSR3JlYXRlciBNYW5jaGVzdGVyMRAwDgYDVQQHDAdTYWxmb3JkMRow
GAYDVQQKDBFDb21vZG8gQ0EgTGltaXRlZDEhMB8GA1UEAwwYQUFBIENlcnRpZmlj
YXRlIFNlcnZpY2VzMB4XDTA0MDEwMTAwMDAwMFoXDTI4MTIzMTIzNTk1OVowezEL
MAkGA1UEBhMCR0IxGzAZBgNVBAgMEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UE
BwwHU2FsZm9yZDEaMBgGA1UECgwRQ29tb2RvIENBIExpbWl0ZWQxITAfBgNVBAMM
GEFBQSBDZXJ0aWZpY2F0ZSBTZXJ2aWNlczCCASIwDQYJKoZIhvcNAQEBBQADggEP
ADCCAQoCggEBAL5AnfRu4ep2hxxNRUSOvkbIgwadwSr+GB+O5AL686tdUIoWMQua
BtDFcCLNSS1UY8y2bmhGC1Pqy0wkwLxyTurxFa70VJoSCsN6sjNg4tqJVfMiWPPe
3M/vg4aijJRPn2jymJBGhCfHdr/jzDUsi14HZGWCwEiwqJH5YZ92IFCokcdmtet4
YgNW8IoaE+oxox6gmf049vYnMlhvB/VruPsUK6+3qszWY19zjNoFmag4qMsXeDZR
rOme9Hg6jc8P2ULimAyrL58OAd7vn5lJ8S3frHRNG5i1R8XlKdH5kBjHYpy+g8cm
ez6KJcfA3Z3mNWgQIJ2P2N7Sw4ScDV7oL8kCAwEAAaOBwDCBvTAdBgNVHQ4EFgQU
oBEKIz6W8Qfs4q8p74Klf9AwpLQwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQF
MAMBAf8wewYDVR0fBHQwcjA4oDagNIYyaHR0cDovL2NybC5jb21vZG9jYS5jb20v
QUFBQ2VydGlmaWNhdGVTZXJ2aWNlcy5jcmwwNqA0oDKGMGh0dHA6Ly9jcmwuY29t
b2RvLm5ldC9BQUFDZXJ0aWZpY2F0ZVNlcnZpY2VzLmNybDANBgkqhkiG9w0BAQUF
AAOCAQEACFb8AvCb6P+k+tZ7xkSAzk/ExfYAWMymtrwUSWgEdujm7l3sAg9g1o1Q
GE8mTgHj5rCl7r+8dFRBv/38ErjHT1r0iWAFf2C3BUrz9vHCv8S5dIa2LX1rzNLz
Rt0vxuBqw8M0Ayx9lt1awg6nCpnBBYurDC/zXDrPbDdVCYfeU0BsWO/8tqtlbgT2
G9w84FoVxp7Z8VlIMCFlA2zs6SFz7JsDoeA3raAVGI/6ugLOpyypEBMs1OUIJqsi
l2D4kF501KKaU73yqWjgom7C12yxow+ev+to51byrvLjKzg6CYG1a4XXvi3tPxq3
smPi9WIsgtRqAEFQ8TmDn5XpNpaYbg==
-----END CERTIFICATE-----
""",
    'emsign-g1.pem': """-----BEGIN CERTIFICATE-----
MIIDlDCCAnygAwIBAgIKMfXkYgxsWO3W2DANBgkqhkiG9w0BAQsFADBnMQswCQYD
VQQGEwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBU
ZWNobm9sb2dpZXMgTGltaXRlZDEcMBoGA1UEAxMTZW1TaWduIFJvb3QgQ0EgLSBH
MTAeFw0xODAyMTgxODMwMDBaFw00MzAyMTgxODMwMDBaMGcxCzAJBgNVBAYTAklO
MRMwEQYDVQQLEwplbVNpZ24gUEtJMSUwIwYDVQQKExxlTXVkaHJhIFRlY2hub2xv
Z2llcyBMaW1pdGVkMRwwGgYDVQQDExNlbVNpZ24gUm9vdCBDQSAtIEcxMIIBIjAN
BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAk0u76WaK7p1b1TST0Bsew+eeuGQz
f2N4aLTNLnF115sgxk0pvLZoYIr3IZpWNVrzdr3YzZr/k1ZLpVkGoZM0Kd0WNHVO
8oG0x5ZOrRkVUkr+PHB1cM2vK6sVmjM8qrOLqs1D/fXqcP/tzxE7lM5OMhbTI0Aq
d7OvPAEsbO2ZLIvZTmmYsvePQbAyeGHWDV/D+qJAkh1cF+ZwPjXnorfCYuKrpDhM
tTk1b+oDafo6VGiFbdbyL0NVHpENDtjVaqSW0RM8LHhQ6DqS0hdW5TUaQBw+jSzt
Od9C4INBdN+jzcKGYEho42kLVACL5HZpIQ15TjQIXhTCzLG3rdd8cIrHhQIDAQAB
o0IwQDAdBgNVHQ4EFgQU++8Nhp6w492pufEhF38+/PB3KxowDgYDVR0PAQH/BAQD
AgEGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQELBQADggEBAFn/8oz1h31x
PaOfG1vR2vjTnGs2vZupYeveFix0PZ7mddrXuqe8QhfnPZHr5X3dPpzxz5KsbEjM
wiI/aTvFthUvozXGaCocV685743QNcMYDHsAVhzNixl03r4PEuDQqqE/AjSxcM6d
GNYIAwlG7mDgfrbESQRRfXBgvKqy/3lyeqYdPV8q+Mri/Tm3R7nrft8EI6/6nAYH
6ftjk4BAtcZsCjEozgyfz7MjNYBBjWzEN3uBL4ChQEKF6dk4jeihU80Bv2noWgby
RQuQ+q7hv53yrlc8pa6yVvSLZUDp/TGBLPQ5Cdjua6e0ph0VpZj3AYHYhX3zUVxx
iN66zB+Afko=
-----END CERTIFICATE-----
""",
    'emsign-g3.pem': """-----BEGIN CERTIFICATE-----
MIICTjCCAdOgAwIBAgIKPPYHqWhwDtqLhDAKBggqhkjOPQQDAzBrMQswCQYDVQQG
EwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNo
bm9sb2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0g
RzMwHhcNMTgwMjE4MTgzMDAwWhcNNDMwMjE4MTgzMDAwWjBrMQswCQYDVQQGEwJJ
TjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNobm9s
b2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0gRzMw
djAQBgcqhkjOPQIBBgUrgQQAIgNiAAQjpQy4LRL1KPOxst3iAhKAnjlfSU2fySU0
WXTsuwYc58Byr+iuL+FBVIcUqEqy6HyC5ltqtdyzdc6LBtCGI79G1Y4PPwT01xyS
fvalY8L1X44uT6EYGQIrMgqCZH0Wk9GjQjBAMB0GA1UdDgQWBBR8XQKEE9TMipuB
zhccLikenEhjQjAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAKBggq
hkjOPQQDAwNpADBmAjEAvvNhzwIQHWSVB7gYboiFBS+DCBeQyh+KTOgNG3qxrdWB
CUfvO6wIBHxcmbHtRwfSAjEAnbpV/KlK6O3t5nYBQnvI+GDZjVGLVTv7jHvrZQnD
+JbNR6iC8hZVdyR+EhCVBCyj
-----END CERTIFICATE-----
""",
}


if __name__ == "__main__":
    unittest.main()
