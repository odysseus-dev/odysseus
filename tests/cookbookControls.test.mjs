import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const cookbook = await readFile(new URL('../static/js/cookbook.js', import.meta.url), 'utf8');
const hwfit = await readFile(new URL('../static/js/cookbook-hwfit.js', import.meta.url), 'utf8');
const diagnosis = await readFile(new URL('../static/js/cookbook-diagnosis.js', import.meta.url), 'utf8');
// Execute production renderers and event handlers with deterministic backend
// adapters. Keep recipe construction, deep links, and form serialization real.
const functions = [
  cookbook.slice(cookbook.indexOf('async function _fetchDependencies('), cookbook.indexOf('// ── Tab wiring')),
  cookbook.slice(cookbook.indexOf('function _renderRecipes('), cookbook.indexOf('// ── Public API')),
  hwfit.slice(hwfit.indexOf('function _manualHwState('), hwfit.indexOf('function _manualHwLabel(')),
  hwfit.slice(hwfit.indexOf('function _wireManualHardwareControls('), hwfit.indexOf('export const _fitColors')),
  diagnosis.slice(diagnosis.indexOf('export function openCookbookDependencies('), diagnosis.indexOf('function _openServeEditFromDiagnosis(')).replace('export ', ''),
].join('\n');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function setup({ installed = false } = {}) {
  const page = await browser.newPage();
  await page.route('http://cookbook.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<style>.hidden{display:none}</style><div id="cookbook-modal"><div class="cookbook-body"></div></div>' });
    const asset = path === '/spinner.js' ? '/static/js/spinner.js' : path;
    return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('..' + asset, import.meta.url), 'utf8') });
  });
  await page.goto('http://cookbook.test/');
  await page.evaluate(async ({ functions, installed }) => {
    const noop = () => {};
    const esc = value => { const el = document.createElement('span'); el.textContent = String(value ?? ''); return el.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;'); };
    Object.assign(window, await import('/static/js/cookbook-deps-recipes.js'));
    Object.assign(window, {
      esc, uiModule: { showToast: value => window.toasts.push(value) }, toasts: [], tasks: [], requests: [],
      _envState: { env: 'none', envPath: '', servers: [], remoteHost: '', hostPlatform: 'linux' },
      _dependenciesFetchId: 0, _dependenciesRequestController: null, _dependenciesModelHint: '',
      _hwfitCache: null, _cachedModelIds: new Set(['MiniMaxAI/MiniMax-M2.7', 'openai/gpt-oss-20b']),
      _loadPresets: () => [], _persistEnvState: noop, _buildServerOpts: () => '<option value="local">Local</option>',
      _serverEntryHtml: () => '', _normalizeCookbookModelDir: x => x,
      _wireTabEvents: noop, _hwfitInit: noop, _resetGpuToggleState: noop,
      _hwfitFetch: () => { window.lastManualParams = window._manualHwParams(); },
      _hwfitRenderHw: noop, _manualDisplaySystem: () => ({}),
      _getPort: () => '', _serverByVal: () => null, _isWindows: () => false,
      _applyServerSelection: noop, _shellQuote: value => "'" + value + "'",
      _MANUAL_HW_KEY: 'hwfit_manual_hardware_v1',
      _addTask: (...args) => window.tasks.push(args),
      _fetchCookbookUiWithTimeout: (...args) => window.fetch(...args),
    });
    (0, eval)(functions + '\nObject.assign(window, {_fetchDependencies, _renderRecipes, _wireManualHardwareControls, _manualHwParams, openCookbookDependencies});');
    _renderRecipes();
    // The tab shell is rendered above; isolate tab navigation from service
    // scans while retaining the real dependency deep-link entry function.
    document.querySelector('[data-backend-group="Dependencies"]').classList.remove('hidden');
    window.cookbookModule = { open: () => {} };
    document.querySelector('[data-backend="Dependencies"]').classList.add('active');
    window.fetch = async (url, init) => {
      if (url.startsWith('/api/cookbook/packages')) return new Response(JSON.stringify({ packages: [
        ...[...RECIPE_BACKENDS].map(name => ({ name, pip: name, desc: name + ' runtime', category: 'LLM', installed, target: 'local' })),
        { name: 'librosa', pip: 'librosa', desc: 'Audio helpers', category: 'Audio', installed: false, target: 'local' },
      ] }));
      if (url === '/api/model/serve') {
        requests.push(JSON.parse(init.body));
        return new Response(JSON.stringify({ ok: true, session_id: 'recipe-task' }));
      }
      throw Error('Unexpected request ' + url);
    };
    await _fetchDependencies();
  }, { functions, installed });
  return page;
}

for (const installed of [false, true]) {
  test(`every ${installed ? 'installed' : 'missing'} recipe backend opens and collapses its own Run panel`, async () => {
    const page = await setup({ installed });
    try {
      const backends = await page.evaluate(() => [...RECIPE_BACKENDS]);
      assert.ok(backends.length > 0);
      for (const backend of backends) {
        const button = page.locator(`[data-dep-recipe-toggle="${backend}"]`);
        const panel = page.locator(`[data-dep-recipe-panel="${backend}"]`);
        assert.equal(await panel.isVisible(), false, backend);
        await button.focus();
        await page.keyboard.press('Enter');
        assert.equal(await panel.isVisible(), true, backend);
        assert.equal(await button.getAttribute('aria-expanded'), 'true');
        assert.equal(await button.getAttribute('aria-controls'), await panel.getAttribute('id'));
        assert.equal(await panel.locator('[data-dep-recipe-run]').isVisible(), true, backend);
        await button.click();
        assert.equal(await panel.isVisible(), false, backend);
        assert.equal(await button.getAttribute('aria-expanded'), 'false');
      }
      assert.equal(await page.locator('[data-pkg-name="librosa"] [data-dep-recipe-toggle]').count(), 0);
      assert.equal(await page.locator('[data-pkg-name="vllm"] .cookbook-dep-installed-btn').count(), installed ? 1 : 0);
    } finally { await page.close(); }
  });
}

test('dependency deep link opens model-specific commands and Run sends the selected variant', async () => {
  const page = await setup();
  try {
    await page.evaluate(() => openCookbookDependencies('vllm', { expandRecipe: 'vllm', model: 'MiniMaxAI/MiniMax-M2.7' }));
    const panel = page.locator('[data-dep-recipe-panel="vllm"]');
    assert.equal(await panel.isVisible(), true);
    assert.equal(await panel.locator('[data-dep-recipe-pick]').inputValue(), 'MiniMaxAI/MiniMax-M2.7');
    // Repeated deep links must leave the panel expanded.
    await page.evaluate(() => openCookbookDependencies('vllm', { expandRecipe: 'vllm', model: 'openai/gpt-oss-20b' }));
    assert.equal(await panel.isVisible(), true);
    assert.equal(await panel.locator('[data-dep-recipe-pick]').inputValue(), 'openai/gpt-oss-20b');
    await panel.locator('[data-variant="docker"]').click();
    const command = await panel.locator('[data-dep-recipe-cmds]').textContent();
    assert.match(command, /docker pull vllm\/vllm-openai:latest/);
    await panel.locator('[data-dep-recipe-run]').click();
    await page.waitForFunction(() => tasks.length === 1);
    const result = await page.evaluate(() => ({ requests, tasks, toasts }));
    assert.deepEqual(result.requests, [{ repo_id: 'vllm', cmd: command }]);
    assert.equal(result.tasks[0][0], 'recipe-task');
    assert.equal(result.tasks[0][1], 'vllm setup');
    assert.equal(result.tasks[0][3].repo_id, 'vllm');
    assert.equal(result.tasks[0][3]._dep, true);
    assert.match(result.toasts.at(-1), /Running vllm setup on local/);
  } finally { await page.close(); }
});

test('pip recipe Run uses a stable backend identity and the catalog install command', async () => {
  const page = await setup();
  try {
    const backends = ['vllm', 'sglang', 'llama_cpp', 'diffusers'];
    for (const backend of backends) {
      await page.locator(`[data-dep-recipe-toggle="${backend}"]`).click();
      await page.locator(`[data-dep-recipe-run="${backend}"]`).click();
    }
    await page.waitForFunction(() => tasks.length === 4);
    const result = await page.evaluate(backends => ({
      requests, tasks,
      expected: backends.map(backend => ({
        repo_id: backend,
        cmd: recipeCommands(pickRecipe(backend, ''), 'pip').join(' && '),
      })),
    }), backends);
    assert.deepEqual(result.requests, result.expected);
    assert.deepEqual(result.tasks.map(task => task[3].repo_id), backends);
    assert.deepEqual(result.tasks.map(task => task[1]), backends.map(backend => backend + ' setup'));
  } finally { await page.close(); }
});

test('manual hardware exposes every backend and preserves its value through Apply and reopening', async () => {
  const page = await setup();
  try {
    await page.evaluate(() => _wireManualHardwareControls(document.getElementById('hwfit-hw')));
    const options = await page.locator('.hwfit-manual-backend option').evaluateAll(nodes => nodes.map(node => ({ value: node.value, label: node.label })));
    assert.deepEqual(options, [
      { value: 'cuda', label: 'CUDA' }, { value: 'rocm', label: 'ROCm' },
      { value: 'metal', label: 'Metal (Apple Silicon)' },
      { value: 'cpu_x86', label: 'CPU (x86)' }, { value: 'cpu_arm', label: 'CPU (ARM)' },
    ]);
    for (const { value } of options) {
      await page.locator('#hwfit-hw-manual-btn').click();
      await page.locator('.hwfit-manual-backend').selectOption(value);
      await page.locator('.hwfit-manual-vram').fill('16');
      await page.locator('.hwfit-manual-ram').fill('32');
      await page.locator('.hwfit-hw-manual-save').click();
      const params = await page.evaluate(() => lastManualParams);
      assert.equal(params.manual_backend, value);
      assert.equal(params.manual_vram_gb, '16');
      assert.equal(params.manual_ram_gb, '32');
      // Rebuild the actual shell, then let the controls restore persisted state.
      await page.evaluate(() => { _renderRecipes(); _wireManualHardwareControls(document.getElementById('hwfit-hw')); });
      assert.equal(await page.locator('.hwfit-manual-backend').inputValue(), value);
    }
  } finally { await page.close(); }
});
