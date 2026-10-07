# Publication asset decisions

Task 2.10-C implements the accepted Task 2.10-B Plan B. Its evidence manifest
SHA-256 is `5090815ec985d9d44e3f23667a28950b51e2e00d6fbadb56a246a50f98352708`.
This decision applies to the candidate tip, not reconstructed history or the
six legacy-public-baseline-only gates.

SAN-158, SAN-159, SAN-160, SAN-161, SAN-163 and SAN-164 retain their exact bytes.
[THIRD_PARTY_PROVENANCE.json](THIRD_PARTY_PROVENANCE.json) ties each artifact to
its upstream identity, archive member, hash and notices in `licenses/`.
Portable/PyInstaller, macOS launcher and Docker packaging include those notices.

SAN-157 replaces the client PDF library with **Print / save PDF**. The browser
opens a print dialog after text and math rendering; saving, cancellation and
pagination belong to the browser. There is no automatic PDF download or promised
layout parity with the former export. Original-document backend conversions,
filled-PDF downloads and Word export remain separate paths.

SAN-162 omits the unused browser QR bundle. Python QR generation for 2FA remains.
SAN-165 omits the unidentified custom font and its unsupported attribution.
Fira Code/monospace is the UI default. Persisted `gohu` and `GohuFont` preferences
map to `mono` in early bootstrap, theme application and the font selector.

SAN-166 replaces both copied catalog snapshots with independently authored
empty lists. See [runtime catalog behavior](services/hwfit/data/README.md).
Tests use synthetic ranking inputs, with factual identifiers retained only where
existing regression tests use them as selectors. Sizes/dates/capabilities are
test inputs, not copied model metadata or production recommendations.

SAN-167 through SAN-174, SAN-176, SAN-178, SAN-180, SAN-182 and SAN-185 through
SAN-190 omit the 18 retained media artifacts listed below. Previously absent
docs video copies remain absent. Feature text remains on the website; playback,
media containers and their CSS/JavaScript are removed. Cookbook backend labels
and controls remain with the blocked decorative marks removed. README branding
uses a text heading. The PWA manifest omits optional icon entries and Apple touch
links; browser installation availability/default presentation can vary. The
macOS launcher uses the system default application icon. Separate out-of-scope
favicon/desktop assets are unchanged; this document does not clear them.

## Removed artifact ledger

The paths below are historical decision records, not runtime resource links.

- SAN-157: `static/lib/html2pdf.bundle.min.js`
- SAN-162: `static/lib/qrcode.min.js`
- SAN-165: `static/fonts/custom/GohuFont.ttf`
- SAN-167: `website/compare.webm`
- SAN-168: `static/icons/ollama-mark-crop.png`
- SAN-169: `website/chat.webm`
- SAN-170: `website/notes.webm`
- SAN-171: `static/icons/sglang-mark.png`
- SAN-172: `assets/branding/odysseus-browser.jpg`
- SAN-173: `static/icons/icon-maskable-512.png`
- SAN-174: `website/gallery.webm`
- SAN-176: `website/bg.webm`
- SAN-178: `static/icons/ollama-mark.png`
- SAN-180: `assets/branding/odysseus.jpg`
- SAN-182: `website/document.webm`
- SAN-185: `static/icons/sglang-logo.png`
- SAN-186: `static/icons/icon-192.png`
- SAN-187: `website/theme.webm`
- SAN-188: `assets/branding/odysseus-wordmark.png`
- SAN-189: `static/icons/icon-512.png`
- SAN-190: `website/research.webm`

SAN-191 reconciles references, font preferences, catalogs, tests and packaging.
The service-worker cache version changes so activation deletes prior app caches.
Omission is not a finding of infringement and does not grant permission to
restore the removed originals.

## Post-publication restoration review

The historical SAN entries recorded above reflect the conservative posture adopted during publication migration. As noted in SAN-191, omission was not a finding of infringement. A subsequent maintainer review traced these 14 project-origin assets through repository history, recorded their repository provenance, and restored their byte-identical historical versions previously distributed by the project.

The original SHA-256 hashes are recorded below. Ambiguous third-party assets (such as vendor marks and third-party libraries) remain deliberately omitted.

- RESTORE-001: `assets/branding/odysseus-browser.jpg` (SHA-256: `96bc894113d3db1a8e19535047e85fef7bfe7e643d45973d6effb945dce667df`): README interface screenshot introduced by pewdiepie-archdaemon in commit "Refresh README screenshot".
- RESTORE-002: `assets/branding/odysseus-wordmark.png` (SHA-256: `5e20708f9709735c66692c0cffc74734d61cfa256d48f9095c6870bcd53f4552`): README wordmark artwork introduced by pewdiepie-archdaemon in commit "Refresh README presentation".
- RESTORE-003: `assets/branding/odysseus.jpg` (SHA-256: `cd5e87b12f1e9e7baca6a6c5e95012130c9e26c6e31526e864eaf17096fcd05e`): Project branding image from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon, used by build-macos-app.sh for launcher icon generation.
- RESTORE-004: `website/bg.webm` (SHA-256: `157f732539a6072d55164da23d65afa9423782728dd067154867fcebb176fb35`): Landing page background video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-005: `website/chat.webm` (SHA-256: `d69f319192cec2cac1831e08ee99ae902398f1b163021e42b3fda05a0d7ab4d5`): Chat demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-006: `website/compare.webm` (SHA-256: `90d8c68dbc2ac3029b258598a7e2c2c5c8f5d6d702b9d31f0c729e5517093f51`): Model comparison demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-007: `website/document.webm` (SHA-256: `754e4ae071e9def5151749a03ad284cd41385354620fc8afea3c5c4f50f64a0d`): Document editor demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-008: `website/gallery.webm` (SHA-256: `b3bbe99c55b5cd1de32cb8e84fa31044761b7724a76a4bae4d4b0acaed7f3500`): Image gallery demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-009: `website/notes.webm` (SHA-256: `2a82b501271c4b60927e575bb22f8c986e3d1a4089a5518f84b46e534dea37d7`): Notes demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-010: `website/research.webm` (SHA-256: `2495a1a6cc1ea037f7460d065e3af6aacf091b240edebc436ce4bca5e83dab87`): Deep research demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-011: `website/theme.webm` (SHA-256: `6bd5a4140e9eeb9e06bee217319d331e9c8c873763fcb9040425fb38878b8410`): Theme demo video from commit e5c99a5eee62fc7df0e2dca5e46cbc87399047d6 ("Odysseus v1.0") by pewdiepie-archdaemon.
- RESTORE-012: `static/icons/icon-192.png` (SHA-256: `d9f54e07d2a8dca6302125d84436176a26a056745c5adb29ef521345c161e359`): Project PWA icon (192x192) contributed in PR #428 (commit 615134851d5c17897b96bba43b7961a00770d45b), generated from the project logo SVG.
- RESTORE-013: `static/icons/icon-512.png` (SHA-256: `785872b140da58087f23539ed187fe0f69de6eb9690abf995b10c93b5b518388`): Project PWA icon (512x512) contributed in PR #428 (commit 615134851d5c17897b96bba43b7961a00770d45b), generated from the project logo SVG.
- RESTORE-014: `static/icons/icon-maskable-512.png` (SHA-256: `7ed567fe0de6b6b451eec35c8fb2b465d84a3b23945135c19c4fa388ac976449`): Project PWA maskable icon (512x512) contributed in PR #428 (commit 615134851d5c17897b96bba43b7961a00770d45b), generated from the project logo SVG.
