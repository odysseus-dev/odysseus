"""Real speech settings controls retain saved providers and custom values."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")
def test_tts_settings_preserve_saved_values_and_toggle_only_enabled():
    script = r'''
        import assert from 'node:assert/strict';
        import { readFile } from 'node:fs/promises';
        import { chromium } from 'playwright';
        const browser = await chromium.launch({ headless: true });
        try {
          for (const endpointsUnavailable of [false, true]) {
            const page = await browser.newPage();
            const posts = [];
            await page.route('http://speech.test/**', async route => {
              const request = route.request();
              const path = new URL(request.url()).pathname;
              if (path === '/api/model-endpoints') return route.fulfill({
                status: endpointsUnavailable ? 503 : 200,
                contentType: 'application/json', body: JSON.stringify(endpointsUnavailable ? [] : [
                  {id:'kokoro', name:'Kokoro', is_enabled:true, model_type:'tts', models:['kokoro']},
                  {id:'disabled', name:'Disabled', is_enabled:false, model_type:'tts', models:[]},
                ]),
              });
              if (path === '/api/auth/settings') {
                if (request.method() === 'POST') {
                  posts.push(request.postDataJSON());
                  return route.fulfill({contentType:'application/json', body:'{}'});
                }
                return route.fulfill({contentType:'application/json', body:JSON.stringify({
                  tts_provider:'endpoint:legacy', tts_model:'kokoro', tts_voice:'af_heart',
                  tts_speed:'1.15', tts_enabled:true,
                })});
              }
              if (path === '/api/tts/clear-cache') return route.fulfill({body:'{}'});
              if (path === '/') {
                const shell = await readFile('./static/index.html', 'utf8');
                return route.fulfill({contentType:'text/html', body:shell.replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, '')});
              }
              if (!path.startsWith('/static/')) return route.fulfill({status:404});
              try {
                const body = await readFile('.' + path);
                const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'application/octet-stream';
                return route.fulfill({contentType, body});
              } catch { return route.fulfill({status:404}); }
            });
            await page.goto('http://speech.test/');
            const state = await page.evaluate(async () => {
              await (await import('/static/js/settings/speech.js')).initTtsSettings();
              const value = id => document.getElementById(id).value;
              const providers = Array.from(document.getElementById('set-ttsProviderSelect').options).map(option => option.value);
              return {provider:value('set-ttsProviderSelect'), model:value('set-ttsModelSelect'),
                      voice:value('set-ttsVoiceSelect'), speed:value('set-ttsSpeedSelect'), providers};
            });
            assert.equal(state.provider, 'endpoint:legacy');
            assert.equal(state.model, 'kokoro');
            assert.equal(state.voice, 'af_heart');
            assert.equal(state.speed, '1.15');
            assert.equal(state.providers.includes('endpoint:kokoro'), !endpointsUnavailable);
            assert.equal(state.providers.includes('endpoint:disabled'), false);
            assert.equal(posts.length, 0);
            await page.evaluate(() => {
              const toggle = document.getElementById('set-ttsEnabledToggle');
              toggle.checked = false;
              toggle.dispatchEvent(new Event('change', {bubbles:true}));
            });
            await page.waitForFunction(() => document.getElementById('set-ttsSettingsMsg').textContent === 'Saved');
            assert.deepEqual(posts, [{tts_enabled:false}]);
            const cacheCleared = page.waitForResponse(response => response.url().endsWith('/api/tts/clear-cache'));
            await page.evaluate(() => {
              const speed = document.getElementById('set-ttsSpeedSelect');
              speed.value = '1.25';
              speed.dispatchEvent(new Event('change', {bubbles:true}));
            });
            await page.waitForFunction(() => document.getElementById('set-ttsSpeedSelect').value === '1.25');
            await cacheCleared;
            assert.deepEqual(posts[1], {tts_enabled:false, tts_provider:'endpoint:legacy',
              tts_model:'kokoro', tts_voice:'af_heart', tts_speed:'1.25'});
            await page.close();
          }
        } finally { await browser.close(); }
    '''
    result = subprocess.run(["node", "--input-type=module", "--eval", script],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
