// Runs the Notes browser flows one at a time, each in isolation: a failing
// flow is recorded and the next one still runs, and whatever browser contexts
// a flow opened are closed when it ends, so a flow that died half-way never
// leaves a page polling or holding a route for the next one. The run ends
// with one summary line, then fails if anything failed.

/** One line per run: counts, then every failure by flow and size. */
export function summaryLine(browserName, results, knownBugs = []) {
  const failed = results.filter(r => !r.ok);
  const seconds = Math.round(results.reduce((sum, r) => sum + r.ms, 0) / 1000);
  const parts = [`SUMMARY ${browserName}: ${results.length} flows, ${results.length - failed.length} passed, ${failed.length} failed`];
  if (failed.length) parts.push(`failed: ${failed.map(r => `${r.flow} (${r.size})`).join(', ')}`);
  parts.push(`known bugs: ${knownBugs.length}`);
  parts.push(`${seconds}s`);
  return parts.join('; ');
}

export function createFlowRunner({ browser, browserName, log = console.log, logError = console.error, now = Date.now }) {
  const results = [];

  /** Run `body` as the flow `flow` at `size`. Never throws: the outcome is recorded. Returns `body`'s value, or undefined if it failed. */
  async function run(flow, size, body) {
    const before = new Set(browser.contexts());
    const started = now();
    let ok = true, error = '', value;
    try {
      value = await body();
    } catch (caught) {
      ok = false;
      error = String(caught?.message || caught).split('\n')[0].slice(0, 300);
      log(JSON.stringify({ failed: flow, browser: browserName, size, error }));
      logError(`[${flow} ${size}]`, caught);
    }
    for (const context of browser.contexts()) {
      if (!before.has(context)) await context.close().catch(() => {});
    }
    results.push({ flow, size, ok, error, ms: now() - started });
    return value;
  }

  /** Print the summary line, then throw if a flow failed or a known bug is still present. */
  function finish(knownBugs = []) {
    log(summaryLine(browserName, results, knownBugs));
    const failed = results.filter(r => !r.ok);
    if (!failed.length && !knownBugs.length) return;
    throw new Error([
      ...(failed.length ? ['Flows that failed:', ...failed.map(r => `${r.flow} (${r.size}): ${r.error}`)] : []),
      ...(knownBugs.length ? ['Known bugs still present (notesBugsFlow.mjs):', ...knownBugs] : []),
    ].join('\n'));
  }

  return { run, finish, results };
}
