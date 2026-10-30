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

function isMobileViewport(testInfo) {
  const width = testInfo.project.use?.viewport?.width || 0;
  return width > 0 && width <= 768;
}

/**
 * Reach Settings through genuine UI controls on either desktop or mobile viewports.
 * On mobile (<=768px), the rail is hidden and the sidebar must be opened via hamburger.
 */
async function openSettings(page, isMobile) {
  let settingsButton;
  if (isMobile) {
    const hamburger = page.locator('#hamburger-btn:visible').first();
    await expect(hamburger, 'Hamburger button must be visible on mobile').toBeVisible({ timeout: 5000 });
    await hamburger.click();
    const sidebar = page.locator('#sidebar:not(.hidden)');
    await expect(sidebar, 'Sidebar must open upon hamburger tap').toBeVisible({ timeout: 5000 });
    await page.waitForTimeout(300);
    settingsButton = sidebar.locator('#user-bar-settings');
  } else {
    settingsButton = page.locator('#rail-settings:visible, #user-bar-settings:visible').first();
  }

  await expect(settingsButton, 'Settings button must be reachable and visible').toBeVisible({ timeout: 5000 });
  await settingsButton.click();
  const dialog = page.locator('#settings-modal');
  await expect(dialog, 'Settings modal must become visible').toBeVisible({ timeout: 5000 });
  return dialog;
}

/**
 * Reach Notes through genuine UI controls on either desktop or mobile viewports.
 */
async function openNotes(page, isMobile) {
  let notesButton;
  if (isMobile) {
    const hamburger = page.locator('#hamburger-btn:visible').first();
    await expect(hamburger, 'Hamburger button must be visible on mobile').toBeVisible({ timeout: 5000 });
    await hamburger.click();
    const sidebar = page.locator('#sidebar:not(.hidden)');
    await expect(sidebar, 'Sidebar must open upon hamburger tap').toBeVisible({ timeout: 5000 });
    await page.waitForTimeout(300);
    notesButton = sidebar.locator('#tool-notes-btn');
  } else {
    notesButton = page.locator('#rail-notes:visible, #tool-notes-btn:visible').first();
  }

  await expect(notesButton, 'Notes button must be reachable and visible').toBeVisible({ timeout: 5000 });
  await notesButton.click();
  const realNotes = page.locator('.notes-pane').first();
  await expect(realNotes, 'Notes pane must become visible').toBeVisible({ timeout: 5000 });
  return realNotes;
}

/**
 * Reach Document editor through genuine UI controls on either desktop or mobile viewports.
 */
async function openDocument(page, isMobile) {
  let docButton;
  if (isMobile) {
    const hamburger = page.locator('#hamburger-btn:visible').first();
    await expect(hamburger, 'Hamburger button must be visible on mobile').toBeVisible({ timeout: 5000 });
    await hamburger.click();
    const sidebar = page.locator('#sidebar:not(.hidden)');
    await expect(sidebar, 'Sidebar must open upon hamburger tap').toBeVisible({ timeout: 5000 });
    await page.waitForTimeout(300);
    docButton = sidebar.locator('#library-new-doc-btn');
  } else {
    docButton = page.locator('#library-new-doc-btn:visible').first();
    if (!(await docButton.count())) {
      const hamburger = page.locator('#hamburger-btn:visible').first();
      if (await hamburger.count()) {
        await hamburger.click();
        await page.waitForTimeout(300);
        docButton = page.locator('#sidebar:not(.hidden) #library-new-doc-btn');
      }
    }
  }

  await expect(docButton, 'Document creation button must be reachable and visible').toBeVisible({ timeout: 5000 });
  await docButton.click();
  const docPane = page.locator('.doc-editor-pane').first();
  await expect(docPane, 'Document editor pane must become visible').toBeVisible({ timeout: 10000 });
  return docPane;
}

test('renders actual Odysseus UI and captures review screenshots', async ({ page }, testInfo) => {
  await openApp(page);
  const out = testInfo.config.metadata.evidenceDir;
  fs.mkdirSync(out, { recursive: true });
  const name = testInfo.project.name;
  const isMobile = isMobileViewport(testInfo);

  // 1. Chat screenshot (default view)
  await page.screenshot({ path: path.join(out, `${name}-chat.png`), fullPage: false, animations: 'disabled' });

  // 2. Settings screenshot via actual user-visible navigation
  await openApp(page);
  await openSettings(page, isMobile);
  await page.screenshot({ path: path.join(out, `${name}-settings.png`), fullPage: false, animations: 'disabled' });

  // 3. Notes screenshot via actual user-visible navigation
  await openApp(page);
  await openNotes(page, isMobile);
  await page.screenshot({ path: path.join(out, `${name}-notes.png`), fullPage: false, animations: 'disabled' });

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

test('CSS cascade rules for mobile Notes and Documents respect safe areas', async ({ page }, testInfo) => {
  test.skip(!isMobileViewport(testInfo), 'Only mobile viewports <=768px');
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
      pane.style.opacity = '0'; // CSS cascade regression check
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

test('real Notes and Document panes honor safe-area geometry in the live application', async ({ page }, testInfo) => {
  const isMobile = isMobileViewport(testInfo);
  test.skip(!isMobile, 'Only mobile viewports <=768px');
  await openApp(page);

  // Configure simulated safe-area insets (e.g. iPhone notch / Dynamic Island)
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--safe-top', '44px');
    document.documentElement.style.setProperty('--sheet-top', '68px');
    document.documentElement.style.setProperty('--app-height', '844px');
  });

  // 1. Verify actual Notes UI
  const realNotes = await openNotes(page, isMobile);
  const notesMetrics = await realNotes.evaluate(el => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      top: style.top,
      height: style.height,
      maxHeight: style.maxHeight,
      rectTop: rect.top,
      rectHeight: rect.height,
    };
  });

  expect(notesMetrics.top, 'Real Notes pane top must match --sheet-top').toBe('68px');
  expect(parseFloat(notesMetrics.height), 'Real Notes pane height must respect safe area').toBeGreaterThanOrEqual(770);
  expect(notesMetrics.rectTop, 'Real Notes pane must not overlap the 44px safe-area top notch').toBeGreaterThanOrEqual(44);

  // 2. Verify actual Document UI
  await openApp(page);
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--safe-top', '44px');
    document.documentElement.style.setProperty('--sheet-top', '68px');
    document.documentElement.style.setProperty('--app-height', '844px');
  });

  const realDoc = await openDocument(page, isMobile);
  const docMetrics = await realDoc.evaluate(el => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      top: style.top,
      height: style.height,
      maxHeight: style.maxHeight,
      rectTop: rect.top,
      rectHeight: rect.height,
    };
  });

  expect(docMetrics.top, 'Real Document pane top must match --sheet-top').toBe('68px');
  expect(parseFloat(docMetrics.height), 'Real Document pane height must respect safe area').toBeGreaterThanOrEqual(770);
  expect(docMetrics.rectTop, 'Real Document pane must not overlap the 44px safe-area top notch').toBeGreaterThanOrEqual(44);
});

test('restored floating windows clamp away from a left safe area', async ({ page }, testInfo) => {
  test.skip(isMobileViewport(testInfo), 'Geometry test uses desktop resizable windows');
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

  expect(value.paddingLeft).toBeGreaterThanOrEqual(58);
  expect(value.width).toBe(480);
  expect(value.left).toBeGreaterThanOrEqual(value.paddingLeft);
});
