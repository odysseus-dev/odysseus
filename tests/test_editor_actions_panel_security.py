import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_action_names_and_unknown_steps_are_literal_text():
    script = r'''
      const fs = require('fs');
      const { chromium } = require('playwright');
      (async () => {
        const browser = await chromium.launch({ headless: true });
        try {
          const page = await browser.newPage();
          await page.route('http://actions.test/**', route => route.fulfill({
            status: 200, contentType: 'text/html', body: '<html><body></body></html>',
          }));
          await page.goto('http://actions.test/');
          const source = fs.readFileSync(process.argv[1], 'utf8');
          const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
          const result = await page.evaluate(async url => {
            const attack = '<img src=x onerror="window.__actionsXss=true">';
            localStorage.setItem('ge-actions', JSON.stringify({ [attack]: ['flip-h'] }));
            const { createActionsPanel } = await import(url);
            const panel = createActionsPanel();
            document.body.append(panel.element);
            panel.setSteps([attack]);
            const input = panel.element.querySelector('.ge-actions-name');
            input.value = attack + '<svg onload="window.__actionsXss=true">';
            input.parentElement.querySelector('button').click();
            const savedName = input.value;
            const savedButton = Array.from(panel.element.querySelectorAll('.ge-actions-saved-row button'))
              .find(button => button.textContent === savedName);
            savedButton.click();
            return {
              images: panel.element.querySelectorAll('img').length,
              svgs: panel.element.querySelectorAll('svg').length,
              saved: panel.element.querySelector('.ge-actions-saved-row button').textContent,
              step: panel.element.querySelector('.ge-actions-step-label').textContent,
              option: Array.from(panel.element.querySelector('.ge-actions-batch-select').options)
                .find(option => option.value === savedName)?.textContent,
              persisted: JSON.parse(localStorage.getItem('ge-actions'))[savedName],
              savedName,
              executed: Boolean(window.__actionsXss),
              attack,
            };
          }, moduleUrl);
          console.log(JSON.stringify(result));
        } finally { await browser.close(); }
      })().catch(error => { console.error(error); process.exit(1); });
    '''
    env = os.environ.copy()
    if 'PLAYWRIGHT_BROWSERS_PATH' not in env:
        for candidate in [Path.home() / '.cache/ms-playwright', Path('/home/alteixeira20/.cache/ms-playwright')]:
            if candidate.exists():
                env['PLAYWRIGHT_BROWSERS_PATH'] = str(candidate)
                break
    result = subprocess.run(
        ['node', '-e', script, str(ROOT / 'static/js/editor/actions-panel.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['images'] == 0
    assert observed['svgs'] == 0
    assert observed['saved'] == observed['step'] == observed['attack']
    assert observed['option'] == observed['savedName']
    assert observed['persisted'] == [observed['attack']]
    assert not observed['executed']
