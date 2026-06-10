# Vendored Static Library Licenses

Third-party assets bundled under `static/lib/` are documented here so their
licensing and versioning are explicit — no undocumented vendored assets.

## Terminal assets (added by this PR — ConPTY terminal)

These back the xterm.js terminal wired to the `/ws/pty` WebSocket
(`static/js/terminal.js`).

| File | Upstream project | npm package | License | Blob bytes (LF) | SHA-256 (LF blob) |
|------|------------------|-------------|---------|-----------------|-------------------|
| `xterm.js` | https://github.com/xtermjs/xterm.js | `xterm` (UMD min) | MIT | 289441 | `1f991ac3b4b283ebf96e60ae23a00a52765dd3a2e46fa6fdda9f1aab032f7495` |
| `xterm-addon-fit.js` | https://github.com/xtermjs/xterm.js — `addons/addon-fit` | `xterm-addon-fit` (UMD min) | MIT | 1497 | `bdaefa370b1bfc42ee88d46fe6072400902a4d4b2d45cd93438dda9b23c97089` |
| `xterm.css` | https://github.com/xtermjs/xterm.js | `xterm` | MIT | 5559 | `ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6` |

**License:** MIT. `xterm.css` carries the full MIT header inline (Copyright (c)
2014 The xterm.js authors; portions Copyright (c) 2012–2013 Christopher
Jeffrey). The minified `.js` UMD bundles ship without an inline banner; the MIT
license reproduced below governs them (same upstream project, same license).

**Versioning:** the `xterm` / `xterm-addon-fit` package names (used **before**
the `@xterm/*` scope rename that landed in xterm.js 5.4) place these in the
xterm.js **5.x** line. The SHA-256 fingerprints above are the authoritative pin
— they uniquely identify the exact bytes vendored and can be verified against
the matching upstream release artifact (npm / jsDelivr).

**Provenance:** minified UMD builds (`sourceMappingURL=xterm.js.map` /
`addon-fit.js.map`); the `.map` sourcemaps are intentionally not vendored.

> Other bundles under `static/lib/` (`docx.umd.min.js`, `highlight.min.js`,
> `html2pdf.bundle.min.js`, `mammoth.browser.min.js`, `qrcode.min.js`,
> `xlsx.full.min.js`) pre-date this PR and are not introduced by it.

### MIT License (xterm.js)

```
Copyright (c) 2014 The xterm.js authors. All rights reserved.
Copyright (c) 2012-2013, Christopher Jeffrey (MIT License)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
