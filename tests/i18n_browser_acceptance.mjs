#!/usr/bin/env node

import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'odysseus-i18n-chrome-'));
const screenshotDir = process.env.I18N_SCREENSHOT_DIR
  ? path.resolve(process.env.I18N_SCREENSHOT_DIR)
  : '';
let browser;
let server;
let configured = true;
const appRoutes = new Set([
  '/', '/calendar', '/cookbook', '/email', '/gallery', '/library', '/memory',
  '/notes', '/tasks',
]);

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

function json(response, value) {
  response.writeHead(200, { 'content-type': 'application/json' });
  response.end(JSON.stringify(value));
}

function startServer() {
  server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (appRoutes.has(url.pathname) || url.pathname === '/login') {
      const page = url.pathname === '/login' ? 'login.html' : 'index.html';
      let html = fs.readFileSync(path.join(ROOT, 'static', page), 'utf8')
        .replaceAll('{{CSP_NONCE}}', 'acceptance');
      if (page === 'login.html') {
        html = html.replace(
          '</body>',
          '<div hidden id="i18n-static-overwrite-probe">Delete this note?</div></body>',
        );
      }
      response.writeHead(200, { 'content-type': contentTypes['.html'] });
      response.end(html);
      return;
    }
    if (url.pathname === '/api/version') return json(response, { version: 'test' });
    if (url.pathname === '/__test/configured') {
      configured = url.searchParams.get('value') !== 'false';
      return json(response, { configured });
    }
    if (url.pathname === '/api/auth/status') {
      return json(response, { authenticated: false, configured, signup_enabled: true });
    }
    if (url.pathname === '/api/auth/policy') {
      return json(response, { password_min_length: 8, reserved_usernames: [] });
    }
    if (url.pathname === '/api/auth/2fa/status') {
      return json(response, { enabled: false });
    }
    if (url.pathname === '/api/auth/login' && request.method === 'POST') {
      return json(response, { requires_totp: true });
    }
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file = path.resolve(ROOT, relative);
    if (!file.startsWith(`${ROOT}${path.sep}`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      response.writeHead(404);
      response.end('not found');
      return;
    }
    response.writeHead(200, {
      'cache-control': 'no-store',
      'content-type': contentTypes[path.extname(file)] || 'application/octet-stream',
    });
    fs.createReadStream(file).pipe(response);
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
}

function startBrowser() {
  return new Promise((resolve, reject) => {
    browser = spawn(process.env.CHROMIUM || 'chromium', [
      '--headless',
      '--no-sandbox',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      '--lang=en-US',
      '--remote-debugging-address=127.0.0.1',
      '--remote-debugging-port=0',
      `--user-data-dir=${profile}`,
      'about:blank',
    ], { stdio: ['ignore', 'ignore', 'pipe'] });
    let output = '';
    const timeout = setTimeout(() => reject(new Error(`Chromium did not start: ${output}`)), 30_000);
    browser.stderr.setEncoding('utf8');
    browser.stderr.on('data', chunk => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (!match) return;
      clearTimeout(timeout);
      resolve(new URL(match[1]).port);
    });
    browser.once('error', error => {
      clearTimeout(timeout);
      reject(error);
    });
    browser.once('exit', code => {
      if (!output.includes('DevTools listening')) {
        clearTimeout(timeout);
        reject(new Error(`Chromium exited before DevTools was ready (${code})`));
      }
    });
  });
}

async function connect(debuggerUrl) {
  const socket = new WebSocket(debuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  let sequence = 0;
  const pending = new Map();
  const eventWaiters = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method && eventWaiters.has(message.method)) {
      const waiters = eventWaiters.get(message.method);
      eventWaiters.delete(message.method);
      waiters.forEach(resolve => resolve(message.params));
    }
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });
  return {
    close: () => socket.close(),
    waitFor(method, timeoutMs = 10_000) {
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error(`timed out waiting for ${method}`)), timeoutMs);
        const done = value => {
          clearTimeout(timeout);
          resolve(value);
        };
        const waiters = eventWaiters.get(method) || [];
        waiters.push(done);
        eventWaiters.set(method, waiters);
      });
    },
    send(method, params = {}) {
      const id = ++sequence;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
  };
}

function valueFrom(result) {
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || 'browser evaluation failed');
  }
  return result.result.value;
}

async function captureScreenshot(cdp, name) {
  if (!screenshotDir) return;
  fs.mkdirSync(screenshotDir, { recursive: true });
  const { data } = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
  });
  fs.writeFileSync(path.join(screenshotDir, name), Buffer.from(data, 'base64'));
}

async function main() {
  await startServer();
  const webPort = server.address().port;
  const devtoolsPort = await startBrowser();
  const pageUrl = `http://127.0.0.1:${webPort}/login`;
  const page = await fetch(
    `http://127.0.0.1:${devtoolsPort}/json/new?${encodeURIComponent('about:blank')}`,
    { method: 'PUT' },
  ).then(response => response.json());
  const cdp = await connect(page.webSocketDebuggerUrl);
  try {
    await cdp.send('Page.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const loaded = cdp.waitFor('Page.loadEventFired');
    await cdp.send('Page.navigate', { url: pageUrl });
    await loaded;
    const evaluation = await cdp.send('Runtime.evaluate', {
      awaitPromise: true,
      returnByValue: true,
      expression: `(async () => {
        for (let attempt = 0; attempt < 200 && !window.odysseusI18n; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        if (!window.odysseusI18n) throw new Error('i18n runtime did not initialize');
        await window.odysseusI18n.ready;
        const i18nResources = () => performance.getEntriesByType('resource')
          .filter(entry => new URL(entry.name).pathname.startsWith('/static/i18n/'));
        const initialFiles = [...new Set(i18nResources().map(
          entry => new URL(entry.name).pathname.split('/').pop(),
        ))].sort();
        const nativeFetch = window.fetch;
        const fallbackFetch = { calls: 0, removed: false };
        window.fetch = async (input, options) => {
          const response = await nativeFetch(input, options);
          if (!String(input).endsWith('/fr.json')) return response;
          fallbackFetch.calls += 1;
          const incomplete = await response.json();
          fallbackFetch.removed = delete incomplete['ui.copy.all.items'];
          return new Response(JSON.stringify(incomplete), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
        };
        await window.odysseusI18n.setLocale('fr', {
          persist: false,
          announce: false,
        });
        window.fetch = nativeFetch;
        const englishFallback = {
          locale: window.odysseusI18n.locale,
          text: window.odysseusI18n.t('ui.copy.all.items'),
          fetch: fallbackFetch,
        };

        await window.odysseusI18n.setLocale('en', {
          persist: false,
          announce: false,
        });
        window.fetch = (input, options) => (
          String(input).endsWith('/de.json')
            ? Promise.reject(new TypeError('synthetic locale load failure'))
            : nativeFetch(input, options)
        );
        let loadErrorMessage = '';
        try {
          await window.odysseusI18n.setLocale('de', {
            persist: false,
            announce: false,
          });
        } catch (error) {
          loadErrorMessage = error.message;
        }
        window.fetch = nativeFetch;
        const loadError = {
          message: loadErrorMessage,
          locale: window.odysseusI18n.locale,
          lang: document.documentElement.lang,
          dir: document.documentElement.dir,
        };

        window.fetch = (input, options) => {
          const delay = String(input).endsWith('/bg.json') ? 120 : 10;
          return new Promise((resolve, reject) => {
            setTimeout(() => nativeFetch(input, options).then(resolve, reject), delay);
          });
        };
        const firstSwitch = window.odysseusI18n.setLocale('bg', {
          persist: false,
          announce: false,
        });
        await new Promise(resolve => setTimeout(resolve, 5));
        const latestSwitch = window.odysseusI18n.setLocale('ar', {
          persist: false,
          announce: false,
        });
        await Promise.all([firstSwitch, latestSwitch]);
        window.fetch = nativeFetch;
        const race = {
          locale: window.odysseusI18n.locale,
          lang: document.documentElement.lang,
          dir: document.documentElement.dir,
        };

        const states = [];
        const timings = [];
        const allStarted = performance.now();
        for (const [id, metadata] of Object.entries(window.odysseusI18n.locales)) {
          const started = performance.now();
          await window.odysseusI18n.setLocale(id, {
            persist: false,
            announce: false,
          });
          timings.push(performance.now() - started);
          states.push({
            requested: id,
            locale: window.odysseusI18n.locale,
            lang: document.documentElement.lang,
            dir: document.documentElement.dir,
            expectedDir: metadata.dir,
            selected: document.getElementById('login-interface-language').value,
            manifest: document.querySelector('link[rel="manifest"]').getAttribute('href'),
            translation: window.odysseusI18n.t('ui.delete.this.note'),
            missingKey: window.odysseusI18n.t('__acceptance_missing_key__'),
          });
        }
        const allElapsed = performance.now() - allStarted;
        const sortedTimings = [...timings].sort((left, right) => left - right);
        const loadedResources = i18nResources();
        const allLocales = {
          states,
          payload: {
            initialFiles,
            loadedFiles: [...new Set(loadedResources.map(
              entry => new URL(entry.name).pathname.split('/').pop(),
            ))].sort(),
            requestCount: loadedResources.length,
            decodedBodyBytes: loadedResources.reduce(
              (total, entry) => total + entry.decodedBodySize,
              0,
            ),
            transferBytes: loadedResources.reduce(
              (total, entry) => total + entry.transferSize,
              0,
            ),
          },
          performance: {
            samples: timings.length,
            totalMs: Math.round(allElapsed * 100) / 100,
            medianMs: Math.round(
              sortedTimings[Math.floor(sortedTimings.length / 2)] * 100,
            ) / 100,
            p95Ms: Math.round(
              sortedTimings[Math.ceil(sortedTimings.length * 0.95) - 1] * 100,
            ) / 100,
            maxMs: Math.round(sortedTimings.at(-1) * 100) / 100,
          },
        };
        await window.odysseusI18n.setLocale('ar', {
          persist: false,
          announce: false,
        });
        await window.odysseusI18n.setLocale('fr', {
          persist: false,
          announce: false,
        });
        allLocales.payload.cachedRepeatRequests = (
          i18nResources().length - loadedResources.length
        );

        const uiProbe = document.createElement('div');
        uiProbe.id = 'i18n-ui-probe';
        uiProbe.setAttribute('data-i18n', 'ui.delete.this.note');
        uiProbe.textContent = 'Delete this note?';
        document.body.appendChild(uiProbe);

        const parameterProbe = document.createElement('div');
        parameterProbe.setAttribute('data-i18n', 'ui.add.value');
        parameterProbe.setAttribute('data-i18n-param-0', '8');
        parameterProbe.textContent = 'Add 8';
        document.body.appendChild(parameterProbe);

        const lateDiv = document.createElement('div');
        lateDiv.textContent = 'Delete this note?';
        document.body.appendChild(lateDiv);
        const lateButton = document.createElement('button');
        lateButton.textContent = 'Sign In';
        document.body.appendChild(lateButton);
        const lateSession = document.createElement('div');
        lateSession.className = 'session-title';
        lateSession.textContent = 'Delete this note?';
        document.body.appendChild(lateSession);

        const rerenderProbe = document.createElement('button');
        rerenderProbe.setAttribute('data-i18n', 'ui.delete.this.note');
        rerenderProbe.textContent = 'Delete this note?';
        document.body.appendChild(rerenderProbe);

        const userProbe = document.createElement('div');
        userProbe.className = 'msg';
        userProbe.innerHTML = '<div class="body">Delete this note?</div>';
        document.body.appendChild(userProbe);

        const directionProbe = document.createElement('textarea');
        directionProbe.value = 'مرحبا';
        document.body.appendChild(directionProbe);

        await new Promise(resolve => setTimeout(resolve, 80));
        const staticProbe = document.getElementById('i18n-static-overwrite-probe');
        const staticInitial = staticProbe.textContent;
        staticProbe.textContent = 'User preferences';
        const rerenderInitial = rerenderProbe.textContent;
        rerenderProbe.textContent = 'Delete this note?';
        await new Promise(resolve => setTimeout(resolve, 40));

        const french = {
          lang: document.documentElement.lang,
          dir: document.documentElement.dir,
          username: document.querySelector('label[for="username"]').textContent.trim(),
          signIn: document.getElementById('submitBtn').textContent.trim(),
          options: document.getElementById('login-interface-language').options.length,
          selected: document.getElementById('login-interface-language').value,
          manifest: document.querySelector('link[rel="manifest"]').getAttribute('href'),
          uiProbe: uiProbe.textContent,
          parameterProbe: parameterProbe.textContent,
          userProbe: userProbe.querySelector('.body').textContent,
          staticInitial,
          lateDiv: lateDiv.textContent,
          lateButton: lateButton.textContent,
          lateSession: lateSession.textContent,
          postLoadRerender: {
            initial: rerenderInitial,
            afterComponentRender: rerenderProbe.textContent,
          },
          direction: directionProbe.dir,
          directionValue: directionProbe.value,
          navigatorLocale: navigator.language,
          formattedNumber: window.odysseusI18n.formatNumber(1234.5),
          frenchNumber: new Intl.NumberFormat('fr').format(1234.5),
          navigatorNumber: new Intl.NumberFormat(navigator.language).format(1234.5),
          formattedDate: window.odysseusI18n.formatDate(
            new Date(Date.UTC(2026, 6, 28)),
            { dateStyle: 'long', timeZone: 'UTC' },
          ),
          frenchDate: new Intl.DateTimeFormat(
            'fr',
            { dateStyle: 'long', timeZone: 'UTC' },
          ).format(new Date(Date.UTC(2026, 6, 28))),
          templateInputs: ['My Notes', 'Class', 'User preferences'].map(
            value => window.odysseusI18n.translateLegacy(value),
          ),
          dynamicDialog: window.odysseusI18n.translateMessage('Delete "Example"?'),
          unknownMessage: window.odysseusI18n.translateMessage('Unknown status'),
        };

        document.getElementById('toggleLink').click();
        await new Promise(resolve => setTimeout(resolve, 50));
        const signup = {
          submit: document.getElementById('submitBtn').textContent.trim(),
          prompt: document.getElementById('toggleText').textContent.trim(),
          link: document.getElementById('toggleLink').textContent.trim(),
        };
        document.getElementById('username').value = 'acceptance-user';
        document.getElementById('password').value = 'a';
        document.getElementById('confirmPassword').value = 'a';
        document.getElementById('authForm').requestSubmit();
        await new Promise(resolve => setTimeout(resolve, 80));
        signup.passwordMinimum = document.getElementById('error').textContent.trim();

        document.getElementById('toggleLink').click();
        document.getElementById('authForm').requestSubmit();
        await new Promise(resolve => setTimeout(resolve, 100));
        const totp = {
          label: document.querySelector('label[for="totp-input"]').textContent.trim(),
          placeholder: document.getElementById('totp-input').placeholder,
          verify: document.getElementById('submitBtn').textContent.trim(),
        };

        await window.odysseusI18n.setLocale('constructor', {
          persist: false,
          announce: false,
        });
        const malicious = {
          locale: window.odysseusI18n.locale,
          lang: document.documentElement.lang,
          inheritedKey: window.odysseusI18n.t('constructor'),
        };

        await window.odysseusI18n.setLocale('ar', {
          persist: false,
          announce: false,
        });
        const arabic = {
          lang: document.documentElement.lang,
          dir: document.documentElement.dir,
          username: document.querySelector('label[for="username"]').textContent.trim(),
        };
        await window.odysseusI18n.setLocale('fr');
        const safety = {
          staticOverwrite: staticProbe.textContent,
          lateDiv: lateDiv.textContent,
          lateButton: lateButton.textContent,
          lateSession: lateSession.textContent,
          semantic: uiProbe.textContent,
          parameter: parameterProbe.textContent,
        };
        return {
          allLocales,
          french,
          signup,
          totp,
          englishFallback,
          loadError,
          malicious,
          race,
          arabic,
          safety,
        };
      })()`,
    });
    const result = valueFrom(evaluation);
    const expectedCatalogFiles = [
      'registry.json',
      ...result.allLocales.states.map(state => `${state.requested}.json`),
    ].sort();
    if (
      result.allLocales.states.length !== 31
      || result.allLocales.states.some(state => (
        state.locale !== state.requested
        || state.lang !== state.requested
        || state.dir !== state.expectedDir
        || state.selected !== state.requested
        || !state.manifest.endsWith(`/static/manifest.${state.requested}.json`)
        || !state.translation
        || (state.requested !== 'en' && state.translation === 'Delete this note?')
        || state.missingKey !== '__acceptance_missing_key__'
      ))
    ) {
      throw new Error(`all-locale runtime switch failed: ${JSON.stringify(result.allLocales)}`);
    }
    if (
      JSON.stringify(result.allLocales.payload.initialFiles)
        !== JSON.stringify(['en.json', 'registry.json'])
      || JSON.stringify(result.allLocales.payload.loadedFiles)
        !== JSON.stringify(expectedCatalogFiles)
      || result.allLocales.payload.requestCount !== expectedCatalogFiles.length
      || result.allLocales.payload.cachedRepeatRequests !== 0
      || result.allLocales.payload.decodedBodyBytes <= 0
    ) {
      throw new Error(`on-demand catalog payload failed: ${JSON.stringify(result.allLocales)}`);
    }
    if (
      result.allLocales.performance.samples !== 31
      || ![
        result.allLocales.performance.totalMs,
        result.allLocales.performance.medianMs,
        result.allLocales.performance.p95Ms,
        result.allLocales.performance.maxMs,
      ].every(value => Number.isFinite(value) && value >= 0)
    ) {
      throw new Error(`locale switch timing failed: ${JSON.stringify(result.allLocales)}`);
    }
    if (result.french.lang !== 'fr' || result.french.dir !== 'ltr') {
      throw new Error(`French document metadata failed: ${JSON.stringify(result.french)}`);
    }
    if (result.french.options !== 31 || result.french.selected !== 'fr') {
      throw new Error(`language selector failed: ${JSON.stringify(result.french)}`);
    }
    if (result.french.username === 'Username' || result.french.signIn === 'Sign In') {
      throw new Error(`French auth strings were not translated: ${JSON.stringify(result.french)}`);
    }
    if (result.french.uiProbe === 'Delete this note?') {
      throw new Error(`semantic UI text did not translate: ${JSON.stringify(result.french)}`);
    }
    if (result.french.parameterProbe === 'Add 8' || !result.french.parameterProbe.includes('8')) {
      throw new Error(`semantic interpolation failed: ${JSON.stringify(result.french)}`);
    }
    if (result.french.userProbe !== 'Delete this note?') {
      throw new Error(`user content was translated: ${JSON.stringify(result.french)}`);
    }
    if (
      result.french.dynamicDialog === 'Delete "Example"?'
      || !result.french.dynamicDialog.includes('Example')
    ) {
      throw new Error(`dynamic dialog did not translate safely: ${JSON.stringify(result.french)}`);
    }
    if (result.french.unknownMessage !== 'Unknown status') {
      throw new Error(`unknown text was translated: ${JSON.stringify(result.french)}`);
    }
    if (
      result.french.lateDiv !== 'Delete this note?'
      || result.french.lateButton !== 'Sign In'
      || result.french.lateSession !== 'Delete this note?'
    ) {
      throw new Error(`late unmarked content was translated: ${JSON.stringify(result.french)}`);
    }
    if (
      result.french.postLoadRerender.initial === 'Delete this note?'
      || result.french.postLoadRerender.afterComponentRender === 'Delete this note?'
    ) {
      throw new Error(`post-load semantic rerender failed: ${JSON.stringify(result.french)}`);
    }
    if (
      result.french.staticInitial === 'Delete this note?'
      || result.french.direction !== 'auto'
      || result.french.directionValue !== 'مرحبا'
      || result.french.formattedNumber !== result.french.frenchNumber
      || result.french.formattedNumber === result.french.navigatorNumber
      || result.french.formattedDate !== result.french.frenchDate
      || JSON.stringify(result.french.templateInputs)
        !== JSON.stringify(['My Notes', 'Class', 'User preferences'])
    ) {
      throw new Error(`static enrollment safety failed: ${JSON.stringify(result.french)}`);
    }
    if (!result.french.manifest.endsWith('/static/manifest.fr.json')) {
      throw new Error(`localized manifest failed: ${JSON.stringify(result.french)}`);
    }
    if (
      result.signup.submit === 'Create Account'
      || result.signup.prompt === 'Already have an account?'
      || result.signup.link.toLowerCase() === 'sign in'
      || result.signup.passwordMinimum === 'Password must be at least 8 characters'
      || !result.signup.passwordMinimum.includes('8')
    ) {
      throw new Error(`dynamic signup localization failed: ${JSON.stringify(result.signup)}`);
    }
    if (
      result.totp.label === '2FA Code'
      || result.totp.placeholder === 'Enter 6-digit code'
      || result.totp.verify === 'Verify'
    ) {
      throw new Error(`dynamic TOTP localization failed: ${JSON.stringify(result.totp)}`);
    }
    if (
      result.englishFallback.locale !== 'fr'
      || result.englishFallback.text !== 'Copy all items'
      || result.englishFallback.fetch.calls !== 1
      || !result.englishFallback.fetch.removed
    ) {
      throw new Error(`English catalog fallback failed: ${JSON.stringify(result.englishFallback)}`);
    }
    if (
      !result.loadError.message.includes('synthetic locale load failure')
      || result.loadError.locale !== 'en'
      || result.loadError.lang !== 'en'
      || result.loadError.dir !== 'ltr'
    ) {
      throw new Error(`failed catalog load changed locale: ${JSON.stringify(result.loadError)}`);
    }
    if (
      result.malicious.locale !== 'en'
      || result.malicious.lang !== 'en'
      || result.malicious.inheritedKey !== 'constructor'
    ) {
      throw new Error(`prototype locale/key fallback failed: ${JSON.stringify(result.malicious)}`);
    }
    if (
      result.race.locale !== 'ar'
      || result.race.lang !== 'ar'
      || result.race.dir !== 'rtl'
    ) {
      throw new Error(`concurrent locale switch race failed: ${JSON.stringify(result.race)}`);
    }
    if (
      result.arabic.lang !== 'ar'
      || result.arabic.dir !== 'rtl'
      || result.arabic.username === 'Username'
    ) {
      throw new Error(`Arabic/RTL switch failed: ${JSON.stringify(result.arabic)}`);
    }
    if (
      result.safety.staticOverwrite !== 'User preferences'
      || result.safety.lateDiv !== 'Delete this note?'
      || result.safety.lateButton !== 'Sign In'
      || result.safety.lateSession !== 'Delete this note?'
      || result.safety.semantic === 'Delete this note?'
      || result.safety.parameter === 'Add 8'
    ) {
      throw new Error(`locale-switch enrollment safety failed: ${JSON.stringify(result.safety)}`);
    }

    if (screenshotDir) {
      const screenshotLoaded = cdp.waitFor('Page.loadEventFired');
      await cdp.send('Page.navigate', { url: pageUrl });
      await screenshotLoaded;
      await cdp.send('Runtime.evaluate', {
        awaitPromise: true,
        expression: `(async () => {
          await window.odysseusI18n.ready;
          await window.odysseusI18n.setLocale('fr', { persist: false, announce: false });
        })()`,
      });
      await captureScreenshot(cdp, 'i18n-login-fr.png');
      await cdp.send('Runtime.evaluate', {
        awaitPromise: true,
        expression: `window.odysseusI18n.setLocale(
          'ar', { persist: false, announce: false }
        )`,
      });
      await captureScreenshot(cdp, 'i18n-login-ar.png');
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: 390,
        height: 844,
        deviceScaleFactor: 1,
        mobile: true,
      });
      await captureScreenshot(cdp, 'i18n-login-ar-mobile.png');
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: 1440,
        height: 900,
        deviceScaleFactor: 1,
        mobile: false,
      });
    }

    await fetch(`http://127.0.0.1:${webPort}/__test/configured?value=false`);
    const setupLoaded = cdp.waitFor('Page.loadEventFired');
    await cdp.send('Page.navigate', { url: pageUrl });
    await setupLoaded;
    const setupEvaluation = await cdp.send('Runtime.evaluate', {
      awaitPromise: true,
      returnByValue: true,
      expression: `(async () => {
        for (let attempt = 0; attempt < 200 && !window.odysseusI18n; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        if (!window.odysseusI18n) throw new Error('setup i18n runtime did not initialize');
        await window.odysseusI18n.ready;
        for (let attempt = 0; attempt < 200 && document.getElementById('setupNote').style.display === 'none'; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        return {
          lang: document.documentElement.lang,
          note: document.getElementById('setupNote').textContent.trim(),
          submit: document.getElementById('submitBtn').textContent.trim(),
          confirmVisible: document.getElementById('confirmGroup').style.display !== 'none',
        };
      })()`,
    });
    result.setup = valueFrom(setupEvaluation);
    if (
      result.setup.lang !== 'fr'
      || result.setup.note === 'First-time setup — create your admin account'
      || result.setup.submit === 'Create Admin Account'
      || !result.setup.confirmVisible
    ) {
      throw new Error(`first-run setup localization failed: ${JSON.stringify(result.setup)}`);
    }
    await fetch(`http://127.0.0.1:${webPort}/__test/configured?value=true`);

    const indexLoaded = cdp.waitFor('Page.loadEventFired');
    await cdp.send('Page.navigate', { url: `http://127.0.0.1:${webPort}/` });
    await indexLoaded;
    const indexEvaluation = await cdp.send('Runtime.evaluate', {
      awaitPromise: true,
      returnByValue: true,
      expression: `(async () => {
        for (let attempt = 0; attempt < 200 && !window.odysseusI18n; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        if (!window.odysseusI18n) throw new Error('index i18n runtime did not initialize');
        await window.odysseusI18n.ready;
        const welcomeTipElement = document.getElementById('welcome-tip');
        const welcomeTipKey = welcomeTipElement.getAttribute('data-i18n');
        const initialWelcomeTip = welcomeTipElement.textContent.trim();
        document.getElementById('incognito-btn').click();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const nobodyWelcomeTip = {
          key: welcomeTipElement.getAttribute('data-i18n'),
          text: welcomeTipElement.textContent.trim(),
          expected: window.odysseusI18n.t(
            'ui.temporary.session.won.t.be.saved.and.no.memory.activation'
          ),
        };
        document.getElementById('incognito-btn').click();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const welcomeTip = {
          key: welcomeTipKey,
          expected: window.odysseusI18n.t(welcomeTipKey),
          initial: initialWelcomeTip,
          nobody: nobodyWelcomeTip,
          restored: {
            key: welcomeTipElement.getAttribute('data-i18n'),
            text: welcomeTipElement.textContent.trim(),
          },
        };
        const settings = (await import('/static/js/settings.js')).default;
        settings.open('account');
        for (let attempt = 0; attempt < 100 && !document.getElementById('tfa-setup-btn'); attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        const twoFactor = document.getElementById('settings-2fa-content').textContent.trim();
        document.getElementById('settings-modal').classList.add('hidden');
        const french = {
          lang: document.documentElement.lang,
          dir: document.documentElement.dir,
          options: document.getElementById('set-interface-language').options.length,
          selected: document.getElementById('set-interface-language').value,
          newChat: document.querySelector('#sidebar-new-chat-btn .grow').textContent.trim(),
          rearrange: document.getElementById('session-rearrange-toggle').childNodes[0].nodeValue.trim(),
          twoFactor,
        };
        await window.odysseusI18n.setLocale('ar', { persist: false, announce: false });
        const sidebar = document.getElementById('sidebar').getBoundingClientRect();
        const handle = document.getElementById('sidebar-resize-handle').getBoundingClientRect();
        const hamburger = document.getElementById('hamburger-btn').getBoundingClientRect();
        const settingsModal = document.getElementById('settings-modal');
        settingsModal.classList.remove('hidden');
        const settingsSidebar = settingsModal.querySelector('.settings-sidebar').getBoundingClientRect();
        const settingsPanels = settingsModal.querySelector('.settings-panels').getBoundingClientRect();
        const settingsNav = settingsModal.querySelector('.settings-nav-item');
        const settingsNavIcon = settingsNav.querySelector('svg').getBoundingClientRect();
        const settingsNavLabel = settingsNav.querySelector('span').getBoundingClientRect();
        settingsModal.classList.add('hidden');
        const rtl = {
          lang: document.documentElement.lang,
          dir: document.documentElement.dir,
          viewportWidth: window.innerWidth,
          sidebarLeft: sidebar.left,
          sidebarRight: sidebar.right,
          sidebarWidth: sidebar.width,
          handleCenter: handle.left + handle.width / 2,
          hamburgerLeft: hamburger.left,
          hamburgerRight: hamburger.right,
          textareaDir: document.getElementById('message').dir,
          inputDir: document.getElementById('model-picker-search').dir,
          settingsSidebarLeft: settingsSidebar.left,
          settingsPanelsRight: settingsPanels.right,
          settingsNavIconLeft: settingsNavIcon.left,
          settingsNavLabelLeft: settingsNavLabel.left,
        };
        await window.odysseusI18n.setLocale('fr', { persist: false, announce: false });
        return { ...french, welcomeTip, rtl };
      })()`,
    });
    result.index = valueFrom(indexEvaluation);
    if (
      result.index.lang !== 'fr'
      || result.index.dir !== 'ltr'
      || result.index.options !== 31
      || result.index.selected !== 'fr'
      || result.index.newChat === 'New Chat'
      || result.index.rearrange === '↑↓ Rearrange'
      || result.index.twoFactor.includes('Add an extra layer of security')
      || result.index.twoFactor.includes('Set Up 2FA')
      || !result.index.welcomeTip.key?.startsWith('ui.welcome.tip.')
      || result.index.welcomeTip.initial !== result.index.welcomeTip.expected
      || result.index.welcomeTip.nobody.key !== 'ui.temporary.session.won.t.be.saved.and.no.memory.activation'
      || result.index.welcomeTip.nobody.text === 'Temporary session — won’t be saved and no memory activation.'
      || result.index.welcomeTip.nobody.text !== result.index.welcomeTip.nobody.expected
      || result.index.welcomeTip.restored.key !== result.index.welcomeTip.key
      || result.index.welcomeTip.restored.text !== result.index.welcomeTip.expected
    ) {
      throw new Error(`localized app shell failed: ${JSON.stringify(result.index)}`);
    }
    if (
      result.index.rtl.lang !== 'ar'
      || result.index.rtl.dir !== 'rtl'
      || Math.abs(result.index.rtl.viewportWidth - result.index.rtl.sidebarRight) > 2
      || result.index.rtl.sidebarWidth < 100
      || Math.abs(result.index.rtl.handleCenter - result.index.rtl.sidebarLeft) > 2
      || result.index.rtl.hamburgerLeft < result.index.rtl.viewportWidth / 2
      || result.index.rtl.viewportWidth - result.index.rtl.hamburgerRight > 20
      || result.index.rtl.textareaDir !== 'auto'
      || result.index.rtl.inputDir !== 'auto'
      || result.index.rtl.settingsSidebarLeft < result.index.rtl.settingsPanelsRight - 2
      || result.index.rtl.settingsNavIconLeft <= result.index.rtl.settingsNavLabelLeft
    ) {
      throw new Error(`Arabic desktop geometry failed: ${JSON.stringify(result.index.rtl)}`);
    }
    if (screenshotDir) {
      await cdp.send('Runtime.evaluate', {
        awaitPromise: true,
        expression: `(async () => {
          await window.odysseusI18n.setLocale('ar', {
            persist: false,
            announce: false,
          });
          const settings = (await import('/static/js/settings.js')).default;
          settings.open('appearance');
          document.querySelectorAll('.toast').forEach(toast => toast.remove());
        })()`,
      });
      await captureScreenshot(cdp, 'i18n-settings-ar.png');
    }
    const routeLoaded = cdp.waitFor('Page.loadEventFired');
    await cdp.send('Page.navigate', { url: `http://127.0.0.1:${webPort}/calendar` });
    await routeLoaded;
    const routeEvaluation = await cdp.send('Runtime.evaluate', {
      awaitPromise: true,
      returnByValue: true,
      expression: `(async () => {
        for (let attempt = 0; attempt < 200 && !window.odysseusI18n; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        await window.odysseusI18n.ready;
        const manifestUrl = document.querySelector('link[rel="manifest"]').href;
        const manifest = await fetch(manifestUrl).then(response => response.json());
        return {
          lang: document.documentElement.lang,
          title: document.title,
          manifestLang: manifest.lang,
          shortName: manifest.short_name,
        };
      })()`,
    });
    result.route = valueFrom(routeEvaluation);
    if (
      result.route.lang !== 'fr'
      || result.route.manifestLang !== 'fr'
      || result.route.title === 'Calendar — Odysseus'
      || result.route.shortName === 'Calendar'
    ) {
      throw new Error(`localized route metadata failed: ${JSON.stringify(result.route)}`);
    }
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } finally {
    cdp.close();
  }
}

try {
  await main();
} finally {
  if (server) await new Promise(resolve => server.close(resolve));
  if (browser && browser.exitCode == null) {
    browser.kill('SIGTERM');
    await new Promise(resolve => {
      const timeout = setTimeout(() => {
        if (browser.exitCode == null) browser.kill('SIGKILL');
        resolve();
      }, 3_000);
      browser.once('exit', () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }
  fs.rmSync(profile, { recursive: true, force: true });
}
