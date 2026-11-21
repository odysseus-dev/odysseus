// Opt-in V8 coverage of the Notes frontend while the browser flows run.
//
// NOTES_JS_COVERAGE_DIR=<dir> makes every page of every context record JS
// coverage (Chromium only: WebKit has no coverage API). When a context or page
// closes, the entries for `/static/js/features/notes/**` are written to <dir>
// in the NODE_V8_COVERAGE format, with URLs pointing at `frontend/src`, the
// source `static/js` is built from. `c8 report --temp-directory <dir>` then
// reads them together with the node unit tests' own NODE_V8_COVERAGE output.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const dir = process.env.NOTES_JS_COVERAGE_DIR;
const PREFIX = '/static/js/features/notes/';
let written = 0;

async function flush(page, { final = true } = {}) {
  if (page.__notesCoverageDone) return;
  if (final) page.__notesCoverageDone = true;
  let entries;
  try {
    entries = await page.coverage.stopJSCoverage();
  } catch {
    return; // the page or its context is already gone
  }
  const result = [];
  for (const entry of entries) {
    let pathname;
    try { pathname = new URL(entry.url).pathname; } catch { continue; }
    if (!pathname.startsWith(PREFIX)) continue;
    const file = path.join(ROOT, 'frontend', 'src', 'features', 'notes', pathname.slice(PREFIX.length));
    if (!fs.existsSync(file)) continue;
    result.push({ scriptId: String(entry.scriptId), url: pathToFileURL(file).href, functions: entry.functions });
  }
  if (!result.length) return;
  written += 1;
  fs.writeFileSync(path.join(dir, `coverage-e2e-${process.pid}-${written}.json`), JSON.stringify({ result }));
}

function instrumentContext(context) {
  const pages = new Set();
  const newPage = context.newPage.bind(context);
  context.newPage = async (...args) => {
    const page = await newPage(...args);
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
    pages.add(page);
    // Chromium drops a document's counts when the page navigates, even with
    // resetOnNavigation off: save them, and start again before the new page loads.
    for (const name of ['reload', 'goto']) {
      const navigate = page[name].bind(page);
      page[name] = async (...navArgs) => {
        await flush(page, { final: false });
        await page.coverage.startJSCoverage({ resetOnNavigation: false }).catch(() => {});
        return navigate(...navArgs);
      };
    }
    const closePage = page.close.bind(page);
    page.close = async (...closeArgs) => { await flush(page); return closePage(...closeArgs); };
    return page;
  };
  const close = context.close.bind(context);
  context.close = async (...args) => {
    for (const page of pages) await flush(page);
    return close(...args);
  };
  return pages;
}

/** Wrap `browser.newContext` so each page records coverage; returns a flush for the end of the run. */
export function instrumentCoverage(browser, browserName) {
  if (!dir || browserName !== 'chromium') return async () => {};
  fs.mkdirSync(dir, { recursive: true });
  const all = [];
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (...args) => {
    const context = await newContext(...args);
    all.push(instrumentContext(context));
    return context;
  };
  return async () => {
    for (const pages of all) for (const page of pages) await flush(page);
  };
}
