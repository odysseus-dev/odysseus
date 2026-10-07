const { test, expect } = require('@playwright/test');

async function addAppStyles(page) {
  const { stylesheetUrls } = await import('../../helpers/stylesheets.mjs');
  for (const url of await stylesheetUrls()) {
    await page.addStyleTag({ url });
  }
}


test('mobile compare uses tabs to show one mounted pane at a time', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/login');
  await addAppStyles(page);

  await page.evaluate(async () => {
    const { default: state } = await import('/static/js/compare/state.js');
    const { mountMobilePaneTabs } = await import('/static/js/compare/panes.js?v=20260903comparemodeicons1');
    document.body.innerHTML = '<main id="compare-test-host" class="chat-container compare-active"><div class="compare-grid" data-cols="3"></div></main>';
    const host = document.getElementById('compare-test-host');
    host.style.cssText = 'position:fixed;inset:0;display:flex;flex-direction:column;padding-top:12px;';
    const grid = host.querySelector('.compare-grid');
    state._blindMode = false;
    state._parallel = true;
    state._selectedModels = [
      { name: 'Alpha model' },
      { name: 'Beta model' },
      { name: 'Gamma model' },
    ];
    state._activeMobilePane = 0;
    state._selectedModels.forEach((model, index) => {
      const pane = document.createElement('section');
      pane.className = 'compare-pane';
      pane.dataset.pane = String(index);
      pane.innerHTML = `<header class="pane-header"><button id="cmp-title-${index}" class="pane-title-btn">${model.name}</button></header><div class="chat-history">Response ${index + 1}</div>`;
      grid.appendChild(pane);
    });
    mountMobilePaneTabs(host, grid);
  });

  const tabs = page.locator('.compare-mobile-tab');
  const panes = page.locator('.compare-pane');
  await expect(tabs).toHaveCount(3);
  await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
  await expect(panes.nth(0)).toBeVisible();
  await expect(panes.nth(1)).toBeHidden();
  await expect(panes.nth(2)).toBeHidden();

  await tabs.nth(1).click();
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await expect(panes.nth(0)).toBeHidden();
  await expect(panes.nth(1)).toBeVisible();
  await expect(panes.nth(2)).toBeHidden();

  const geometry = await page.evaluate(() => ({
    grid: document.querySelector('.compare-grid').getBoundingClientRect().toJSON(),
    pane: document.querySelector('.compare-pane-mobile-active').getBoundingClientRect().toJSON(),
    visiblePaneCount: Array.from(document.querySelectorAll('.compare-pane')).filter((pane) => getComputedStyle(pane).display !== 'none').length,
  }));
  expect(geometry.visiblePaneCount).toBe(1);
  expect(Math.abs((geometry.grid.width - 16) - geometry.pane.width)).toBeLessThan(2);
});


test('mobile compare probe keeps feedback below models and actions split', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/login');
  await addAppStyles(page);
  await page.evaluate(() => {
    document.body.innerHTML = `
      <div class="compare-probe-overlay">
        <section class="compare-probe-card">
          <div class="compare-probe-title">Checking models...</div>
          <div class="compare-probe-list">
            <div class="compare-probe-row"><span class="compare-probe-spinner">▁▂▃</span><span class="compare-probe-name">Alpha</span></div>
            <div class="compare-probe-row fail"><span class="compare-probe-spinner fail">×</span><span class="compare-probe-name">Beta</span></div>
          </div>
          <div class="compare-probe-feedback">
            <div class="compare-probe-detail"><span class="compare-probe-detail-message">Insufficient balance</span><button class="compare-probe-action-btn"><svg></svg><span>Retry</span></button><button class="compare-probe-action-btn"><svg></svg><span>Swap</span></button></div>
          </div>
          <div class="compare-probe-footer"><button class="cmp-btn-secondary compare-probe-footer-btn">Go Back</button><button class="cmp-btn-primary compare-probe-footer-btn compare-probe-start-anyway">Start Anyway</button></div>
        </section>
      </div>`;
  });

  const layout = await page.evaluate(() => {
    const list = document.querySelector('.compare-probe-list').getBoundingClientRect();
    const feedback = document.querySelector('.compare-probe-feedback').getBoundingClientRect();
    const back = document.querySelector('.compare-probe-footer-btn').getBoundingClientRect();
    const start = document.querySelector('.compare-probe-start-anyway').getBoundingClientRect();
    const spinnerStyle = getComputedStyle(document.querySelector('.compare-probe-spinner'));
    const cardStyle = getComputedStyle(document.querySelector('.compare-probe-card'));
    const startStyle = getComputedStyle(document.querySelector('.compare-probe-start-anyway'));
    return {
      feedbackBelowList: feedback.top >= list.bottom,
      splitActions: back.left < start.left && start.right > 350,
      spinnerTransform: spinnerStyle.transform,
      cardRadius: cardStyle.borderRadius,
      startBackground: startStyle.backgroundColor,
    };
  });
  expect(layout.feedbackBelowList).toBe(true);
  expect(layout.splitActions).toBe(true);
  expect(layout.spinnerTransform).toContain('-2');
  expect(layout.cardRadius).toBe('8px');
  expect(layout.startBackground).not.toBe('rgba(0, 0, 0, 0)');
});
