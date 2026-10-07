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
