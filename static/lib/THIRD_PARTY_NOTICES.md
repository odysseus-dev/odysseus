# Third-party notices — `static/lib/`

These front-end libraries are vendored (committed) and served directly so the
app renders fully offline / self-hosted. Each is the unmodified upstream
distribution. License texts that ship with the bundle live next to it; the
project-wide inventory is in [`ACKNOWLEDGMENTS.md`](../../ACKNOWLEDGMENTS.md).

| Library | Version | Files | License | Source | License text |
|---|---|---|---|---|---|
| [KaTeX](https://github.com/KaTeX/KaTeX) | 0.16.22 | `katex/katex.min.css`, `katex/katex.min.js`, `katex/fonts/` | MIT — © 2013–2020 Khan Academy and other contributors | `npm: katex@0.16.22` | [`katex/LICENSE`](katex/LICENSE) |
| [Mermaid](https://github.com/mermaid-js/mermaid) | 11.15.0 | `mermaid.min.js` | MIT — © 2014–2022 Knut Sveidqvist | `npm: mermaid@11.15.0` | [`mermaid.LICENSE.txt`](mermaid.LICENSE.txt) |

The bundles are the exact files published to npm (fetched via `npm pack`), kept
byte-for-byte so their provenance stays verifiable. `.gitattributes` marks them
`-whitespace linguist-vendored` so whitespace checks and language stats don't
treat the minified upstream output as project source.

Other libraries already vendored here (highlight.js, SheetJS/xlsx, docx,
mammoth.js, html2pdf.js, node-qrcode) are credited in
[`ACKNOWLEDGMENTS.md`](../../ACKNOWLEDGMENTS.md).
