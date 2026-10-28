"""Discovery-only probe results must not claim a completion/latency test."""
import json
import subprocess

import pytest
from tests.helpers.stylesheets import stylesheet_link_tags


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('mixed', [False, True])
def test_probe_display_distinguishes_listed_from_tested_models(width, mixed):
    events = []
    if mixed:
        events += [
            {'type':'probe_start','endpoint':'Local','model_count':1,'hidden_skipped':1},
            {'type':'probe_result','model':'local-model','status':'ok','latency_ms':25},
        ]
    events += [
        {'type':'probe_start','endpoint':'API','model_count':1,'hidden_skipped':1,'discovery_only':True},
        {'type':'probe_result','model':'api-model','status':'ok','latency_ms':None,'skipped':True},
        {'type':'probe_done','total':2 if mixed else 1,'ok':2 if mixed else 1,'skipped':1},
    ]
    stream = ''.join('data: '+json.dumps(e)+'\n\n' for e in events)
    script = r"""
      import { chromium } from 'playwright';
      const browser = await chromium.launch({headless:true});
      try {
        const page = await browser.newPage({viewport:{width:WIDTH,height:1000}});
        await page.route('**/api/**',route => new URL(route.request().url()).pathname === '/api/probe'
          ? route.fulfill({contentType:'text/event-stream',body:STREAM})
          : route.fulfill({json:{}}));
        await page.goto(process.env.ODYSSEUS_TEST_STATIC_ORIGIN+'/static/js/storage.js');
        await page.setContent('__STYLES__<div id="chat-history"></div>');
        const result = await page.evaluate(async () => {
          const module = await import('/static/js/slashCommands.js');
          module.initSlashCommands({apiBase:''});
          await module.COMMANDS.probe.handler([],{esc:s=>String(s)});
          return document.querySelector('#chat-history').innerText;
        });
        console.log(JSON.stringify(result));
      } finally { await browser.close(); }
    """
    script = script.replace('WIDTH', str(width)).replace('STREAM', json.dumps(stream))
    script = script.replace('__STYLES__', stylesheet_link_tags())
    result = subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    text = json.loads(result.stdout.strip())
    assert 'not tested' in text and 'listed without completion probes' in text
    assert 'disabled skipped' in text
    assert '0ms' not in text
    if mixed:
        assert '1/1 models responding (100%)' in text
        assert '2/2 models responding' not in text
    else:
        assert 'No completion probes run' in text
        assert 'models responding' not in text
