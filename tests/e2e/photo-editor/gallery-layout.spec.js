const { test, expect } = require('@playwright/test');

test('gallery photos body uses tall viewport without cropping or stretching cards', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.route('**/api/gallery/library?**', async route => {
    const pixel = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="160" height="160"%3E%3Crect width="160" height="160" fill="%23666"/%3E%3C/svg%3E';
    const items = Array.from({ length: 60 }, (_, index) => ({
      id: `layout-${index}`,
      url: pixel,
      thumbnail_url: pixel,
      filename: `Layout ${index}.png`,
      prompt: `Layout ${index}`,
      model: 'imported',
      created_at: '2026-08-31T00:00:00Z',
    }));
    await route.fulfill({ json: { items, total: items.length, has_more: false } });
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#tool-gallery-btn').waitFor({ state: 'attached', timeout: 20_000 });
  await page.locator('#tool-gallery-btn').click();
  await expect(page.locator('#gallery-modal')).toBeVisible({ timeout: 20_000 });

  await expect(page.locator('#gallery-grid .gallery-card')).toHaveCount(61);
  await expect(page.locator('#gallery-grid')).not.toHaveClass(/gallery-just-opened/, { timeout: 2_000 });
  const layout = await page.evaluate(() => {
    const modal = document.querySelector('.gallery-modal-content').getBoundingClientRect();
    const body = document.querySelector('#gallery-modal .modal-body').getBoundingClientRect();
    const grid = document.querySelector('#gallery-grid').getBoundingClientRect();
    const upload = document.querySelector('#gallery-upload-tile').getBoundingClientRect();
    const cards = [...document.querySelectorAll('#gallery-grid .gallery-card')]
      .slice(0, 12)
      .map(card => card.getBoundingClientRect());
    return { modal, body, grid, upload, cards, viewportHeight: innerHeight };
  });

  expect(layout.grid.height).toBeGreaterThan(layout.viewportHeight * 0.65);
  expect(layout.grid.bottom).toBeLessThanOrEqual(layout.body.bottom + 1);
  expect(Math.abs(layout.upload.width - layout.upload.height)).toBeLessThanOrEqual(2);
  for (let i = 0; i < layout.cards.length; i += 1) {
    for (let j = i + 1; j < layout.cards.length; j += 1) {
      const a = layout.cards[i];
      const b = layout.cards[j];
      const overlaps = a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      expect(overlaps).toBe(false);
    }
  }
});
