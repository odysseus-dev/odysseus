const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function openApp(page) {
  const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
  expect(response?.ok()).toBeTruthy();
  // The runtime currently produces two #message nodes. Select the
  // initialized composer while diagnosing whether both are visible.
  const composers = await page.locator('textarea#message').evaluateAll(nodes =>
    nodes.map((el, index) => ({
      index,
      initialized: el.dataset.startupPreserveBound === '1',
      visible: !!el.getClientRects().length &&
        getComputedStyle(el).visibility !== 'hidden',
      parent: el.parentElement?.className || '',
    }))
  );
  if (composers.length !== 1) {
    console.log('Composer DOM diagnostic:', JSON.stringify(composers));
  }
  const activeComposer = page.locator(
    'textarea#message[data-startup-preserve-bound="1"]'
  );
  await expect(activeComposer).toHaveCount(1);
  await expect(activeComposer).toBeVisible();
  expect(
    composers.filter(composer => composer.visible).length,
    'Exactly one composer should be visible'
  ).toBe(1);
}

test('renders actual Odysseus UI and captures review screenshots', async ({ page }, testInfo) => {
  await openApp(page);
  const out = testInfo.config.metadata.evidenceDir;
  fs.mkdirSync(out, { recursive: true });
  const name = testInfo.project.name;
  await page.screenshot({ path: path.join(out, `${name}-chat.png`), fullPage: false, animations: 'disabled' });

  // Screenshot the actual Settings UI only if a real user-visible Settings
  // control is reachable; never fabricate a modal for PR screenshots.
  const settingsButton = page.locator('#rail-settings:visible, #user-bar-settings:visible').first();
  if (await settingsButton.count()) {
    await settingsButton.click();
    const dialog = page.locator('#settings-modal');
    if (await dialog.isVisible()) {
      await page.screenshot({ path: path.join(out, `${name}-settings.png`), fullPage: false, animations: 'disabled' });
    }
  }

  // Open the real Notes route via an existing UI action when it is reachable.
  // These screenshots reflect genuine runtime content, not test-injected panes.
  await openApp(page);
  const notesButton = page.locator('#rail-notes:visible, #tool-notes-btn:visible').first();
  if (await notesButton.count()) {
    await notesButton.click();
    const realNotes = page.locator('.notes-pane:visible').first();
    if (await realNotes.count()) {
      await page.screenshot({ path: path.join(out, `${name}-notes.png`), fullPage: false, animations: 'disabled' });
    }
  }
  testInfo.annotations.push({ type: 'evidence', description: out });
});

test('viewport meta does not restrict user zoom', async ({ page }) => {
  await openApp(page);
  const meta = await page.locator('meta[name="viewport"]').getAttribute('content');
  expect(meta || '').not.toMatch(/maximum-scale\s*=\s*1(?:\b|\.)/i);
  expect(meta || '').not.toMatch(/user-scalable\s*=\s*no/i);
});

test('browser-owned window scrolling is not globally reset', async ({ page }) => {
  await openApp(page);
  const value = await page.evaluate(async () => {
    document.documentElement.style.overflowY = 'auto';
    document.body.style.overflowY = 'auto';
    document.body.style.height = 'auto';
    document.body.style.minHeight = '3200px';
    const spacer = document.createElement('div');
    spacer.style.cssText = 'position:absolute;top:3100px;width:1px;height:10px;pointer-events:none;';
    document.body.appendChild(spacer);
    window.scrollTo(0, 250);
    await new Promise(resolve => setTimeout(resolve, 150));
    return window.scrollY;
  });
  expect(value).toBeGreaterThan(100);
});

test('mobile Notes and Documents panes honor the safe-area top', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'phone-webkit-portrait', 'Only mobile <=768px');
  await openApp(page);
  const results = await page.evaluate(() => {
    document.documentElement.style.setProperty('--safe-top', '44px');
    document.documentElement.style.setProperty('--sheet-top', '68px');
    document.documentElement.style.setProperty('--app-height', '844px');
    const values = {};
    for (const [key, bodyClass, paneClass] of [
      ['notes', 'notes-view', 'notes-pane'],
      ['document', 'doc-view', 'doc-editor-pane'],
    ]) {
      const pane = document.createElement('section');
      pane.className = paneClass;
      pane.style.opacity = '0'; // CSS-only fixture, not a screenshot of fake app content
      document.body.appendChild(pane);
      document.body.classList.add(bodyClass);
      const style = window.getComputedStyle(pane);
      values[key] = {
        top: style.top,
        height: style.height,
        insetTop: pane.getBoundingClientRect().top,
      };
      pane.remove();
      document.body.classList.remove(bodyClass);
    }
    return values;
  });
  for (const key of ['notes', 'document']) {
    expect(results[key].top, key).toBe('68px');
    expect(parseFloat(results[key].height), key).toBeGreaterThanOrEqual(770);
  }
});

test('restored floating windows clamp away from a left safe area', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'Geometry test uses desktop resizable windows');
  await openApp(page);
  const value = await page.evaluate(async () => {
    document.documentElement.style.setProperty('--safe-left', '58px');
    document.body.style.setProperty('padding-left', '58px', 'important');
    const probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;left:10px;top:80px;width:420px;height:250px;z-index:999999;';
    document.body.appendChild(probe);
    const key = 'odysseus-layout-restore-test';
    localStorage.setItem(key, JSON.stringify({ w: 480, h: 280 }));
    try {
      // Body horizontal padding transitions over 160ms. Wait for the
      // simulated safe-area change to settle before restoring geometry.
      await new Promise(resolve => setTimeout(resolve, 300));

      const padAtRestore = parseFloat(
        getComputedStyle(document.body).paddingLeft
      );
      console.log('PADDING AT RESTORE:', padAtRestore);

      if (Math.abs(padAtRestore - 58) > 0.5) {
        throw new Error(
          `Safe-area fixture did not settle: ${padAtRestore}px`
        );
      }

      // Verify the running server actually serves the patched module.
      const response = await fetch(
        '/static/js/windowResize.js',
        { cache: 'no-store' }
      );
      if (!response.ok) {
        throw new Error('Cannot fetch windowResize.js');
      }
      const moduleSource = await response.text();
      if (!moduleSource.includes('const rightmostLeft = Math.max(padLeft')) {
        throw new Error('Running server is serving unpatched windowResize.js');
      }

      const { makeWindowResizable } = await import('/static/js/windowResize.js');
      makeWindowResizable(probe, { storageKey: key, mobileSkip: 768 });
      await new Promise(resolve => setTimeout(resolve, 300));
      return {
        left: parseFloat(probe.style.left),
        width: parseFloat(probe.style.width),
        paddingLeft: parseFloat(getComputedStyle(document.body).paddingLeft),
        viewportWidth: window.innerWidth,
      };
    } finally {
      localStorage.removeItem(key);
      probe.remove();
    }
  });
  console.log('WINDOW RESTORE DIAGNOSTIC:', JSON.stringify(value));
  expect(value.paddingLeft).toBeGreaterThanOrEqual(58);
  expect(value.width).toBe(480);
  expect(value.left).toBeGreaterThanOrEqual(value.paddingLeft);
});
