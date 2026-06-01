// i18n coverage tour — automated answer to "does the catalog match the real UI?"
//
// Logs in, switches locale with debug on, walks the main surfaces, then dumps
// every UI string that rendered with NO translation (window.i18n.report()).
// Turns the manual ?i18ndebug click-through into a repeatable pass; run it in CI
// with STRICT=1 to fail when new untranslated strings appear.
//
// One-time setup:  npm i -D playwright && npx playwright install chromium
// Run:
//   ODY_USER=admin ODY_PASS=... node scripts/i18n/coverage.mjs
// Env:
//   BASE_URL  (default http://127.0.0.1:7077)
//   LOCALE    (default ja)        ODY_USER / ODY_PASS  (login creds)
//   OUT       (file to write the list; default: stdout only)
//   STRICT=1  (exit 1 if any untranslated strings were found)

import { writeFileSync } from "node:fs";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:7077";
const LOCALE = process.env.LOCALE || "ja";
const USER = process.env.ODY_USER || "admin";
const PASS = process.env.ODY_PASS || "";
const OUT = process.env.OUT || "";
const STRICT = process.env.STRICT === "1";

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error("playwright not installed. Run: npm i -D playwright && npx playwright install chromium");
  process.exit(2);
}

const safe = async (label, fn) => {
  try { await fn(); } catch (e) { console.error(`  (skipped ${label}: ${e.message})`); }
};
const pause = (p, ms = 250) => p.waitForTimeout(ms);

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

try {
  // ---- log in ----
  await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#username", USER);
  await page.fill("#password", PASS);
  await Promise.all([
    page.waitForURL((u) => !/\/login/.test(u.toString()), { timeout: 15000 }).catch(() => {}),
    page.click("#submitBtn"),
  ]);
  await page.waitForSelector("#rail-settings", { timeout: 15000 });

  // ---- switch locale with debug collector on, then reload so init applies it ----
  await page.evaluate(([loc]) => {
    localStorage.setItem("odysseus-i18n-debug", "1");
    localStorage.setItem("odysseus-locale", loc);
  }, [LOCALE]);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.i18n && window.i18n.getLocale, null, { timeout: 15000 });
  await page.evaluate(async () => { await window.i18n.ready; });
  await pause(page, 400);

  // ---- tour the main surfaces (tolerant; extend with your own clicks) ----
  await safe("settings", async () => {
    await page.click("#rail-settings");
    await page.waitForSelector("#settings-modal:not(.hidden)", { timeout: 5000 });
    for (const tab of await page.$$("[data-settings-tab]")) {
      await tab.click().catch(() => {});
      await pause(page, 200);
    }
    await page.keyboard.press("Escape");
  });
  await safe("composer overflow", async () => {
    await page.click("#overflow-plus-btn");
    await pause(page, 200);
    await page.keyboard.press("Escape");
  });
  await safe("model picker", async () => {
    await page.click("#model-picker-btn, [id*='model-picker']");
    await pause(page, 200);
    await page.keyboard.press("Escape");
  });

  // ---- collect ----
  const missing = await page.evaluate(() => window.i18n.report());
  console.log(`\ni18n coverage for "${LOCALE}" @ ${BASE_URL}`);
  console.log(`untranslated strings rendered during tour: ${missing.length}`);
  for (const s of missing) console.log(`  ${JSON.stringify(s)}`);
  if (OUT) {
    writeFileSync(OUT, missing.join("\n") + "\n", "utf8");
    console.log(`\nwrote ${missing.length} -> ${OUT}`);
  }
  console.log("\nTip: add each to static/locales/<code>.json (and en.json).");

  await browser.close();
  if (STRICT && missing.length) process.exit(1);
} catch (e) {
  console.error("coverage run failed:", e.message);
  await browser.close().catch(() => {});
  process.exit(2);
}
