# Chromium incident distrust policy

`chromium-emudhra.json` is installed in the Docker image at `/etc/chromium/policies/managed/odysseus-emudhra.json`. Chromium 132 and later support `CADistrustedCertificates`: a list of base64-encoded DER X.509 certificates whose public keys must not be accepted for TLS server authentication. Matching is by SubjectPublicKeyInfo (SPKI), so one certificate per compromised key also blocks its cross-signs. This policy adds no trust, disables no certificate checks, and does not require a revocation-list download. Native browsers and other browser distributions require separately managed installation of the appropriate policy.

Policy semantics: [Chromium's version-155 definition](https://github.com/chromium/chromium/blob/155.0.8059.79/components/policy/resources/templates/policy_definitions/CertificateManagement/CADistrustedCertificates.yaml). Linux installation paths: [Chromium policy documentation](https://www.chromium.org/administrators/configuring-policy-for-extensions/#linux).

Public certificate source, retrieved 2026-10-21: [incident intermediate inventory](https://emudhra.we-have-a-problem.com/impacted-intermediates.json). Its downloaded JSON has SHA-256 `f67b3abe9b4879f4e91874ed041beccd71257f1db41c588c724ccf12aa90c59e`. Each selected certificate's DER fingerprint and public-key fingerprint were checked against that inventory before inclusion. The eight unique keys below are covered, including the code-signing intermediate keys listed in the incident. The policy contains only public certificates; no private key is included.

| Intermediate | SHA-256 of DER SPKI |
| --- | --- |
| EM OV TLS CA - G3B | `43bffa19d6ef651451f6841e019931e96f6f2eaeff28ab4b6f1ce4576de2717b` |
| EM DV TLS CA - G3C | `49dd353dc2b919a3770645117d0e60c16115e7d1bc259eefe734b00ea434fe5f` |
| EM EV TLS CA - G3C | `5c0660a2b164910d8ff589c18e14552e9e0ec237dcefe2cb736d41a4811f81a0` |
| EM OV TLS CA - G3C | `63ecefee431d49523bc1136c4e72d0206a477086caf606cd5cf025686c109cf8` |
| EM OV CS CA - G3C | `92c063d67e8f14db71d288e11266ffdaf69cbf115f7bab14f3bc2f68d25e0e30` |
| EM EV TLS CA - G3B | `e0254c7fcdc265e2f0f8a428dabb75ab4ae039b4872d62f5ee57f0877d5a8a41` |
| EM DV TLS CA - G3B | `ebed6475c2c2d80fcb199f3fe8dd6e6b3ce60b77eb15d4e42c3dec0c6f5b5105` |
| EM OV CS CA - G3B | `ec4ffa1ad0ac88081314b7aaffef6a91f97cd57291bb5ba686c5415f5eb4f6b6` |

`tests/test_chromium_ca_policy.py` verifies that the policy distrusts exactly these eight keys using valid intermediate CA certificates and adds no trust settings. Changes to the encoded material need a fresh source/fingerprint check and behavioral validation: all twelve published incident HTTPS chains must be rejected by the actual Chromium binary while ordinary HTTPS still succeeds. Confirm loading and any policy errors at `chrome://policy`. Do not treat a passing PEM-store check as a browser-policy check.
