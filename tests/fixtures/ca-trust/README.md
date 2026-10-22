# CA trust regression fixtures

The public certificates embedded in `tests/test_ca_trust.py` exercise the October 2026 eMudhra trust-store check. They contain no private keys. `emsign-g3.pem` and `aaa.pem` are the incident's affected trust anchors; `emsign-g1.pem` is an unaffected emSign anchor that must remain usable. `localhost-leaf.pem` is a synthetic self-signed Ed25519 server certificate with `CA:FALSE`, generated solely to reproduce hashed non-CA certificates found in Ubuntu's OpenSSL directory; its private key was discarded. The test creates temporary PEM files from these public constants. The certificate fingerprints are the factual identity of each fixture.

Sources: the [incident root list](https://emudhra.we-have-a-problem.com/roots.json) and the public Mozilla root certificates distributed in Debian's `ca-certificates` package.

`incident.json` contains the four published roots, sixteen [compromised intermediate certificates and cross-signs](https://emudhra.we-have-a-problem.com/impacted-intermediates.json), and the [twelve published server chains](https://emudhra.we-have-a-problem.com/chains.zip), retrieved on 2026-10-21. Certificates are deduplicated by SHA-256 of DER; each chain lists its leaf first. The additional cross-signed `emSign Root TLS CA - G3` issuer comes from those chains and has fingerprint `3094025792039a68af6e553686410bdab7c0d4a36b9084cba64202e35d6ebbdd`. Like the intermediates, it must not be accepted as a custom trust anchor. All data is public certificate material, with no private keys.

Downloaded source SHA-256 values:

- `roots.json`: `119f32cd81a6b5a4e3c4c2df5fe07daa90dc43cafa123a62f0394f1895d92380`
- `impacted-intermediates.json`: `f67b3abe9b4879f4e91874ed041beccd71257f1db41c588c724ccf12aa90c59e`
- `chains.zip`: `8a2edc81b6c51e3ad27d45ca1e441ec84c961e2dee0fc547f2a02c0f74315f95`

The offline OpenSSL regression verifies server purpose, hostname and strict certificate rules with partial-chain trust, at `2026-10-21T12:00:00Z` (`1792584000`). Fixing the verification date keeps later certificate expiry from masking a broken mitigation. All twelve chains must verify against the affected positive-control roots and fail against the unaffected G1-only store. Adding the compromised DV G3B intermediate restores two paths, which the checker must reject. The regression makes no network requests and skips chain verification if the OpenSSL executable is unavailable; certificate-inventory tests still run using Python's standard library.
