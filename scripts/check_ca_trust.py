#!/usr/bin/env python3
"""Reject the eMudhra incident's known CA certificates in PEM trust stores.

This checks certificate contents, including OpenSSL's lazily loaded CA directory.
It does not model native OS certificate revocation or arbitrary custom clients.
Incident fingerprints: https://emudhra.we-have-a-problem.com/roots.json
and https://emudhra.we-have-a-problem.com/impacted-intermediates.json
Cross-signed issuer: https://emudhra.we-have-a-problem.com/chains.zip
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import re
import ssl
import sys


BLOCKED_CERTIFICATES = {
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b": "emSign ECC Root CA - G3",
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4": "AAA Certificate Services",
    "7dd78d5f4f13459a83dff9abbba62edbaf6f2d102bf257fd712f4d9f2746ed8d": "emSign Root TLS CA - G3",
    "f2edaaff3d9b701fb4d14c9f285f8c0bad90fc3bcc1fa07d47f4718edaba34a5": "emSign Root CS CA - G3",
    "3094025792039a68af6e553686410bdab7c0d4a36b9084cba64202e35d6ebbdd": "emSign Root TLS CA - G3 (cross-signed)",
    # Intermediates can themselves be operator-supplied trust anchors. Include
    # both published signatures for each key, without parsing SPKI in stdlib.
    "42c4accd12c4f59597925e1a08a7a90102608543809308d50ec427e118ba1493": "EM DV TLS CA - G3B (eMudhra)",
    "45a883f349aedfd5f3a649b5498ffb121712ba8b448f3f2a91dbc5a766fcddbe": "EM DV TLS CA - G3B (Sectigo)",
    "98b896625c0c230adeec3eb2cf6aad5001ecf9156a68644e82374ce5833bc25b": "EM OV TLS CA - G3B (eMudhra)",
    "5f1ded4dd590af74631290cf073b122a2aac6a1fa479778a0bd43579bd832699": "EM OV TLS CA - G3B (Sectigo)",
    "e7568fe6e12331f4fd7de61da4c7559d0f8eb192a218d4741a3ba75184333838": "EM EV TLS CA - G3B (eMudhra)",
    "579480aea166290bd3c643589e795fc0d0e54e86505d180f1dff99efd3199512": "EM EV TLS CA - G3B (Sectigo)",
    "0164d7ceae5bb909c272af151ff44b1177ae9b47d9424b94f1d9558c30934d19": "EM DV TLS CA - G3C (eMudhra)",
    "62b7f9868263f72f6911fc78ceaf72c1979158b7368bf648683bb2aa39b02e72": "EM DV TLS CA - G3C (Sectigo)",
    "cb6e15165ac802e9b3f993f16c25d41f4e81c21ebde71ae1b012ac00bf9c7c3f": "EM OV TLS CA - G3C (eMudhra)",
    "7485b51c0ed192878fa1d2f948fb529ae1842cc5086c4b67f7be5b2039de9489": "EM OV TLS CA - G3C (Sectigo)",
    "432ec25f0d28de9006d97bf752f17bbb2cd41f669933fcb072d9a1148d0261f3": "EM EV TLS CA - G3C (eMudhra)",
    "fd9d5c2dbbd4995ab69b8d50c439d9527b00529199c2fd01b2174eb58f07021f": "EM EV TLS CA - G3C (Sectigo)",
    "9e39de2881273acc225a149c72ba3e0f51424963669ab633ee1dc7df5db067b4": "EM OV CS CA - G3B (eMudhra)",
    "a496e23ac0cab8155fc9b117b97eae368efcbb9041aec8804d5a308d2d01c2d8": "EM OV CS CA - G3B (Sectigo)",
    "4b2460a466f95fd40113bcdcca2ceb29b3b093921de45474da7df48874c630b1": "EM OV CS CA - G3C (eMudhra)",
    "84112972b8e59d14560ae7e7119654474aad119990cf3722bfc1e3b91615e1fd": "EM OV CS CA - G3C (Sectigo)",
}
OVERRIDE_ENV_VARS = (
    "LLM_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "PIP_CERT", "NODE_EXTRA_CA_CERTS", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
)


def check_bundle(path: Path) -> None:
    """Raise on an unreadable, empty, malformed, or affected CA bundle."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(path))
    # A hashed OpenSSL directory can include valid non-CA certificates (for
    # example Ubuntu's snakeoil server certificate). get_ca_certs() omits them;
    # use the total X.509 count to distinguish these from an empty store.
    if not context.cert_store_stats()["x509"]:
        raise ValueError("no certificates")
    certificates = context.get_ca_certs(binary_form=True)
    blocked = {
        BLOCKED_CERTIFICATES[fingerprint]
        for certificate in certificates
        if (fingerprint := hashlib.sha256(certificate).hexdigest()) in BLOCKED_CERTIFICATES
    }
    if blocked:
        raise ValueError("affected CA certificates: " + ", ".join(sorted(blocked)))


def ca_directory_paths(directory: Path) -> list[Path]:
    """Return the hashed certificate files consumed by OpenSSL's CApath."""
    return [
        path for path in directory.iterdir()
        if re.fullmatch(r"[0-9a-fA-F]{8}\.[0-9]+", path.name) and path.is_file()
    ]


def system_paths() -> list[Path]:
    """Inspect both the effective OpenSSL CA file and hashed directory entries."""
    defaults = ssl.get_default_verify_paths()
    paths = [Path(defaults.cafile)] if defaults.cafile else []
    # get_default_verify_paths() can report None for a colon-separated
    # SSL_CERT_DIR even though OpenSSL consumes each directory in that list.
    capath = os.environ.get(defaults.openssl_capath_env, defaults.capath)
    if capath:
        for directory in filter(None, capath.split(os.pathsep)):
            paths.extend(ca_directory_paths(Path(directory)))
    if not paths:
        raise ValueError("no effective OpenSSL PEM CA store found")
    return paths


def override_paths() -> list[Path]:
    """Inspect every configured override, including client fallback settings."""
    paths = []
    for name in OVERRIDE_ENV_VARS:
        value = os.environ.get(name, "")
        if name == "LLM_CA_BUNDLE":
            value = value.strip()  # Match src/tls_overrides.py.
        if not value:
            continue
        path = Path(value)
        # Requests (also used by pip) accepts either a file or a hashed directory.
        if name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT") and path.is_dir():
            entries = ca_directory_paths(path)
            if not entries:
                raise ValueError(f"{name}: no hashed certificates in {path}")
            paths.extend(entries)
        else:
            paths.append(path)
    return paths


def grpc_roots_path() -> Path:
    """Select gRPC's override or its independent Python-wheel default bundle."""
    override = os.environ.get("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH")
    if override:
        # gRPC can fall back to its bundled roots when this file is unreadable.
        # Check the configured file strictly instead of blessing that fallback.
        return Path(override)
    spec = importlib.util.find_spec("grpc")
    if spec is None or spec.origin is None:
        raise ImportError("grpcio is not installed; cannot check its default roots")
    return Path(spec.origin).parent / "_cython" / "_credentials" / "roots.pem"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles", nargs="*", type=Path)
    parser.add_argument("--system", action="store_true", help="check effective OpenSSL PEM stores")
    parser.add_argument("--python", action="store_true", help="check certifi and pip's separate vendored bundle")
    parser.add_argument("--grpc", action="store_true", help="check gRPC's selected default CA file")
    parser.add_argument("--overrides", action="store_true", help="check configured LLM/HTTPX/Requests/pip/Node/gRPC CA overrides")
    parser.add_argument("--verbose", action="store_true", help="show the runtime identity, Python executable and selected CA paths")
    args = parser.parse_args(argv)
    if not (args.bundles or args.system or args.python or args.grpc or args.overrides):
        parser.error("select --system, --python, --grpc, --overrides, or explicit PEM bundle paths")
    if args.verbose:
        print(f"Python executable: {sys.executable}")
        print(f"Python prefix: {sys.prefix}")
        print(f"User home: {Path.home()}")
        if hasattr(os, "geteuid"):
            print(f"Effective user: uid={os.geteuid()} gid={os.getegid()}")
    try:
        paths = list(args.bundles)
        if args.system:
            paths.extend(system_paths())
        if args.python:
            for name in ("certifi", "pip._vendor.certifi"):
                paths.append(Path(importlib.import_module(name).where()))
        if args.grpc:
            paths.append(grpc_roots_path())
        if args.overrides:
            paths.extend(override_paths())
        paths = list(dict.fromkeys(path.resolve() for path in paths))
        if not paths:
            raise ValueError("no CA bundle files selected; also select --system, --python, or an explicit path")
        errors = []
        for path in paths:
            if args.verbose:
                print(f"CA bundle: {path}")
            try:
                check_bundle(path)
            except (OSError, ValueError, ssl.SSLError) as exc:
                errors.append(f"{path}: {exc}")
        if errors:
            raise ValueError("\n".join(errors))
    except (ImportError, OSError, ValueError, ssl.SSLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Validated {len(paths)} CA bundle file(s): no known eMudhra incident CA certificates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
