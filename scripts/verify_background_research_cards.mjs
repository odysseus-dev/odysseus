/** Card layout and reconciliation against the served assets; no user mutations. */
import { chromium } from 'playwright';

const ORIGIN = 'http://127.0.0.1:7011';

/** The `<link rel="stylesheet">` tags the app shell ships, in shell order. */
async function shellStylesheets() {
  const response = await fetch(`${ORIGIN}/static/index.html`);
  if (!response.ok) throw new Error(`static/index.html returned ${response.status}`);
  const links = (await response.text()).match(/<link\b[^>]*rel=["']stylesheet["'][^>]*>/gi) || [];
  if (!links.length) throw new Error('no stylesheet links found in static/index.html');
  return links.join('');
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(`${ORIGIN}/static/test-fixtures/browser-catalog.html`);
  // Read the shell's stylesheets out of index.html rather than naming one
  // here. style.css is now a set of ordered fragments, and a hardcoded link
  // to a file that has moved does not fail - it renders unstyled and the
  // layout checks below pass against nothing.
  await page.setContent(`${await shellStylesheets()}<main style="padding:16px"><div id="chat-history"><p>Existing conversation</p></div></main>`);
  const checks = await page.evaluate(async () => {
    const { renderResearchCards } = await import('/static/js/backgroundToolJobs.js');
    const box = document.querySelector('#chat-history');
    const first = box.firstElementChild;
    const job = { id: 'rp-card-fixture', tool: 'research', query: 'Why Boston terriers are best <img src=x onerror=alert(1)>', status: 'running', rounds: 2, progress: { phase: 'reading', round: 1, total_sources: 3 } };
    renderResearchCards(box, [job]);
    const card = box.querySelector('.chat-research-card');
    const header = card.querySelector('.agent-thread-header');
    const collapsed = header.getAttribute('aria-expanded') === 'false';
    header.click();
    const link = card.querySelector('a');
    link.focus();
    renderResearchCards(box, [job]);
    const result = {
      repeat_poll_preserves_card_and_focus: card === box.querySelector('.chat-research-card') && document.activeElement === link,
      no_html_injection: !card.querySelector('img'),
      research_deeplink: link.getAttribute('href') === '#research-rp-card-fixture',
      live_stage: card.textContent.includes('Round 1/2 · 3 sources'),
      transcript_preserved: box.firstElementChild === first,
      collapsed_by_default: collapsed,
      disclosure_preserved_on_poll: header.getAttribute('aria-expanded') === 'true' && card.classList.contains('open'),
      running_whirlpool: Boolean(card.querySelector('[data-research-spinner] canvas')),
      uses_existing_timeline: box.querySelector('.background-tools-status').classList.contains('agent-thread'),
    };
    renderResearchCards(box, [{ ...job, status: 'delivered', outcome: 'no_sources', source_count: 0 }]);
    result.failure_is_visible = card.textContent.includes('No sources found') && !card.querySelector('[data-research-spinner] canvas');
    renderResearchCards(box, [job, { ...job, id: 'rp-done-fixture', query: 'A completed research topic', status: 'delivered', outcome: 'complete', source_count: 4 }, { ...job, id: 'rp-empty-fixture', query: 'A run with no evidence', status: 'delivered', outcome: 'no_sources', source_count: 0 }]);
    return result;
  });
  await page.waitForTimeout(300);
  checks.mobile_no_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  checks.touch_target = await page.locator('.chat-research-open').first().evaluate(el => el.getBoundingClientRect().height >= 44);
  const header = page.locator('.chat-research-card .agent-thread-header').first();
  await header.focus();
  await page.keyboard.press('Enter');
  checks.keyboard_collapse = await header.getAttribute('aria-expanded') === 'false';
  await page.keyboard.press('Space');
  checks.keyboard_expand = await header.getAttribute('aria-expanded') === 'true';
  checks.right_side_background_spinner = await page.locator('.chat-research-card').first().evaluate(el => {
    const bg = el.querySelector('.chat-research-background').getBoundingClientRect();
    const status = el.querySelector('[data-stage]').getBoundingClientRect();
    return bg.left >= status.right && Boolean(el.querySelector('[data-research-spinner] canvas'));
  });
  await page.screenshot({ path: '/tmp/odysseus-research-cards-mobile.png', fullPage: true });
  console.log(JSON.stringify(checks));
  if (!Object.values(checks).every(Boolean)) process.exitCode = 1;
} finally { await browser.close(); }
