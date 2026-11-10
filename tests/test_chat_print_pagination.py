"""The shipped CSS must print every message beyond the screen viewport."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="node is required for Chromium printing")
def test_chat_print_preserves_the_complete_transcript(tmp_path):
    script = r'''
      import fs from 'node:fs';
      import path from 'node:path';
      import { chromium } from 'playwright';
      const origin = process.env.ODYSSEUS_TEST_STATIC_ORIGIN;
      const source = fs.readFileSync('static/index.html', 'utf8');
      const links = [...source.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)]
        .map(match => match[0]).join('\n');
      const messages = Array.from({length: 24}, (_, index) => {
        const marker = 'EXPORTSENTINEL' + String(index + 1).padStart(2, '0');
        const paragraphs = Array.from({length:4}, () =>
          '<p>This synthetic message must survive printing beyond the screen viewport. '
          + 'Every paragraph belongs to the saved conversation.</p>').join('');
        return `<div class="msg ${index % 2 ? 'msg-ai' : 'msg-user'}"><div class="body"><p>${marker}</p>${paragraphs}</div></div>`;
      }).join('');
      const browser = await chromium.launch({headless:true});
      try {
        const results=[];
        for (const [layout,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]) {
          const page=await browser.newPage({viewport});
          await page.goto(origin+'/static/css/00-tokens.css');
          await page.setContent(`<html><head>${links}</head><body><main class="chat-container"><div id="chat-history">${messages}</div></main></body></html>`,{waitUntil:'load'});
          await page.evaluate(()=>document.fonts.ready);
          const file=path.join(process.env.ODYSSEUS_PDF_OUTPUT_DIR,layout+'.pdf');
          await page.pdf({path:file,format:'A4',printBackground:true});
          results.push({layout,file});
          await page.close();
        }
        console.log(JSON.stringify(results));
      } finally {await browser.close();}
    '''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        capture_output=True, text=True, timeout=90,
        env={**os.environ, "ODYSSEUS_PDF_OUTPUT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    results = json.loads(result.stdout)
    assert {row["layout"] for row in results} == {"desktop", "mobile"}
    for row in results:
        pdf = PdfReader(row["file"])
        text = "".join("".join(page.extract_text().split()) for page in pdf.pages)
        assert len(pdf.pages) > 1, row["layout"]
        for index in range(1, 25):
            assert f"EXPORTSENTINEL{index:02d}" in text, (row["layout"], index)
