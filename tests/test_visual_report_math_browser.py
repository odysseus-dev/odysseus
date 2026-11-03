"""Exercise generated report HTML with the real vendored KaTeX in Chromium."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from src.visual_report import generate_visual_report


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("mode", ["math", "plain", "blocked"])
def test_report_math_in_browser(tmp_path, width, mode):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    markdown = (
        "# Math report\n\n" + r"## Formula $x_i$" + "\n\n"
        + r"Inline \(x_i^2\) and $a < b$." + "\n\n"
        + r"\[\sum_{i=1}^{3} x_i\]" + "\n\n"
        + r"Invalid: $\frac{1}$. Unsafe: $\href{javascript:alert(1)}{bad}$."
        + "\n\n`$literal$` costs $5 to $10."
        + "\n\n$$" + "+".join(["x_i"] * 30) + "$$"
    ) if mode != "plain" else "## Findings\n\nNo math here."
    document = tmp_path / "report.html"
    document.write_text(generate_visual_report("Math report", markdown), encoding="utf-8")
    script = r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
let chromium;
try { ({chromium} = require('playwright')); }
catch (error) { if (error.code === 'MODULE_NOT_FOUND') process.exit(77); throw error; }
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:Number(process.argv[2]),height:900}});
    const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
    const errors = [], requests = [], failures = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => requests.push(r.url()));
    page.on('requestfailed', r => failures.push(r.url()));
    page.on('dialog', async d => { errors.push('Unexpected dialog'); await d.dismiss(); });
    const mode = process.argv[3];
    if (mode === 'blocked') await page.route('**/static/lib/katex/katex.min.js', r => r.abort());
    await page.route(origin + '/report-math-test', r => r.fulfill({
      contentType:'text/html', body:fs.readFileSync(process.argv[1], 'utf8')
    }));
    await page.goto(origin + '/report-math-test');
    if (mode === 'math') {
      await page.locator('.content .katex-display').first().waitFor({state:'attached'});
      await page.waitForFunction(() => !document.querySelector('.ody-math-pending'));
      assert.equal(await page.locator('.toc-sidebar .katex').count(), 1);
      assert.equal(await page.locator('.content .katex-error').count(), 1);
      assert.equal(await page.locator('.content code').innerText(), '$literal$');
      assert((await page.locator('.content').innerText()).includes('$5 to $10'));
      assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
      assert.equal(requests.filter(u => u.endsWith('/static/lib/katex/katex.min.js')).length, 1);
      assert.equal(requests.filter(u => u.endsWith('/static/lib/katex/katex.min.css')).length, 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(failures, []);
    } else if (mode === 'blocked') {
      await page.waitForFunction(() => performance.getEntriesByType('resource').some(e => e.name.includes('katex.min.js')));
      assert((await page.locator('.content .ody-math-pending').first().innerText()).includes('x_i'));
      assert.equal(await page.locator('.katex').count(), 0);
    } else {
      await page.waitForLoadState('networkidle');
      assert.equal(requests.some(u => u.includes('/static/lib/katex/')), false);
      assert.equal(requests.some(u => u.includes('/static/js/markdown.js')), false);
    }
    assert.deepEqual(errors, []);
    assert(requests.every(u => u.startsWith(origin + '/')), JSON.stringify(requests));
    console.log(JSON.stringify({mode, width:process.argv[2], requests:requests.length}));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
"""
    result = subprocess.run(
        ["node", "-e", script, str(document), str(width), mode],
        cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 77:
        pytest.skip("playwright not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["mode"] == mode
