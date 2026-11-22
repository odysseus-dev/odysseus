import test from 'node:test';
import assert from 'node:assert/strict';
import { createFlowRunner, summaryLine } from './browser/notesRunner.mjs';

// A stand-in for a Playwright browser: contexts() and context.close() only.
function fakeBrowser() {
  const open = new Set();
  return {
    open,
    contexts: () => [...open],
    newContext() {
      const context = { closed: false, async close() { this.closed = true; open.delete(this); } };
      open.add(context);
      return context;
    },
  };
}

function quietRunner(browser) {
  const lines = [];
  let clock = 0;
  const runner = createFlowRunner({ browser, browserName: 'chromium', log: line => lines.push(line), logError: () => {}, now: () => (clock += 1000) });
  return { runner, lines };
}

test('a failing flow is recorded and every later flow still runs', async () => {
  const { runner } = quietRunner(fakeBrowser());
  const ran = [];
  await runner.run('native', 'phone', async () => { ran.push('native'); throw new Error('element is outside of the viewport\ncall log...'); });
  await runner.run('sharing', 'desktop', async () => { ran.push('sharing'); });
  await runner.run('history', 'phone', async () => { ran.push('history'); });
  assert.deepEqual(ran, ['native', 'sharing', 'history']);
  assert.deepEqual(runner.results.map(r => [r.flow, r.size, r.ok, r.error]), [
    ['native', 'phone', false, 'element is outside of the viewport'],
    ['sharing', 'desktop', true, ''],
    ['history', 'phone', true, ''],
  ]);
});

test('the contexts a flow opened are closed when it ends, pass or fail; older ones stay', async () => {
  const browser = fakeBrowser();
  const { runner } = quietRunner(browser);
  const outer = browser.newContext();
  let left;
  await runner.run('sharing', 'phone', async () => { left = [browser.newContext(), browser.newContext()]; throw new Error('pill'); });
  assert.deepEqual(left.map(c => c.closed), [true, true]);
  await runner.run('history', 'desktop', async () => { left = browser.newContext(); });
  assert.equal(left.closed, true);
  assert.equal(outer.closed, false);
  assert.deepEqual(browser.contexts(), [outer]);
});

test('a setup step that fails returns undefined instead of ending the run', async () => {
  const { runner } = quietRunner(fakeBrowser());
  assert.equal(await runner.run('setup', 'stub model', async () => { throw new Error('stub down'); }), undefined);
  assert.equal(await runner.run('setup', 'stranger account', async () => 'stranger'), 'stranger');
});

test('finish prints one summary line, and throws only when something failed', async () => {
  const { runner, lines } = quietRunner(fakeBrowser());
  await runner.run('native', 'desktop', async () => {});
  await runner.run('history', 'phone', async () => { throw new Error('timed out waiting for the diff'); });
  await runner.run('sharing', 'phone', async () => {});
  assert.throws(() => runner.finish(['share pill dropped']), error => {
    assert.match(error.message, /^Flows that failed:\nhistory \(phone\): timed out waiting for the diff\nKnown bugs still present/);
    return true;
  });
  assert.equal(lines.at(-1), 'SUMMARY chromium: 3 flows, 2 passed, 1 failed; failed: history (phone); known bugs: 1; 3s');

  const green = quietRunner(fakeBrowser());
  await green.runner.run('native', 'desktop', async () => {});
  green.runner.finish([]);
  assert.equal(green.lines.at(-1), 'SUMMARY chromium: 1 flows, 1 passed, 0 failed; known bugs: 0; 1s');
});

test('the summary of a run with known bugs only still fails the run', () => {
  assert.equal(summaryLine('webkit', [{ flow: 'ui', size: 'phone', ok: true, ms: 2500 }], ['a', 'b']), 'SUMMARY webkit: 1 flows, 1 passed, 0 failed; known bugs: 2; 3s');
  const browser = fakeBrowser();
  const { runner } = quietRunner(browser);
  assert.throws(() => runner.finish(['a']), /Known bugs still present/);
});
