"""DOM-XSS regressions for email plain-text linkification helpers."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emailLibrary" / "utils.js"
_HAS_NODE = shutil.which("node") is not None


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_plain_text_linkify_escapes_href_attribute_without_double_escaping():
    js = textwrap.dedent(
        f"""
        globalThis.document = {{
          createElement() {{
            return {{
              set textContent(v) {{
                this._t = String(v ?? '')
                  .replace(/&/g, '&amp;')
                  .replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;')
                  .replace(/'/g, '&#39;');
              }},
              get innerHTML() {{ return this._t || ''; }}
            }};
          }}
        }};
        const {{ _escLinkify }} = await import('{_HELPER.as_posix()}');
        const out = _escLinkify('See https://example.test/path?a=1&b=2 and www.example.test/a`b');
        console.log(JSON.stringify(out));
        """
    )

    html = json.loads(_run(js))

    assert 'href="https://example.test/path?a=1&amp;b=2"' in html
    assert "amp;amp" not in html
    assert 'href="https://www.example.test/a&#96;b"' in html


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_email_url_scheme_checks_strip_embedded_controls():
    js = textwrap.dedent(
        f"""
        import fs from 'node:fs';

        let source = fs.readFileSync('{_HELPER.as_posix()}', 'utf8');
        source = source
          .replace('function _compactUrlSchemeValue', 'export function _compactUrlSchemeValue')
          .replace('function _isDangerousUrl', 'export function _isDangerousUrl')
          .replace('function _isDangerousSrcset', 'export function _isDangerousSrcset');

        const mod = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
        const checks = {{
          compact: mod._compactUrlSchemeValue('java\\n script:\\talert(1)'),
          jsUrl: mod._isDangerousUrl('java\\n script:\\talert(1)'),
          vbUrl: mod._isDangerousUrl('vb\\rscript:msgbox(1)'),
          dataUrl: mod._isDangerousUrl(' data:text/html,<script>alert(1)</script>'),
          httpUrl: mod._isDangerousUrl('https://example.test/?q=javascript:alert(1)'),
          srcset: mod._isDangerousSrcset('https://safe.test/a.png 1x, java\\nscript:alert(1) 2x'),
        }};
        console.log(JSON.stringify(checks));
        """
    )

    checks = json.loads(_run(js))

    assert checks["compact"] == "javascript:alert(1)"
    assert checks["jsUrl"] is True
    assert checks["vbUrl"] is True
    assert checks["dataUrl"] is True
    assert checks["httpUrl"] is False
    assert checks["srcset"] is True


def test_email_html_sanitizer_runs_to_fixpoint():
    source = _HELPER.read_text(encoding="utf-8")

    assert "function _sanitizeHtmlOnce(html)" in source
    assert "for (let i = 0; i < 4; i++)" in source
    assert "const next = _sanitizeHtmlOnce(out);" in source
    assert "if (next === out) break;" in source
