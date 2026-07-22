/* Real-browser smoke coverage for PR #4661.

Run against a local AUTH_ENABLED=false app with Playwright available:
  ODYSSEUS_TEST_URL=http://127.0.0.1:17000 node tests/browser_memory_lifecycle.js
*/

const { firefox } = require('playwright');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const browser = await firefox.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const url = process.env.ODYSSEUS_TEST_URL || 'http://127.0.0.1:17000';

  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.chatModule && window.sessionModule, null, { timeout: 30000 });

  const results = await page.evaluate(async () => {
    const box = document.getElementById('chat-history');
    const addGroups = () => {
      box.innerHTML = '';
      for (let group = 0; group < 90; group++) {
        for (let part = 0; part < 2; part++) {
          const node = document.createElement('div');
          node.className = part ? 'agent-thread' : 'msg msg-ai';
          node.dataset.domTrimGroup = `group-${group}`;
          node.textContent = `${group}:${part}`;
          box.appendChild(node);
        }
      }
    };
    const groupCounts = () => Array.from(box.children).reduce((counts, node) => {
      const group = node.dataset.domTrimGroup || '(none)';
      counts[group] = (counts[group] || 0) + 1;
      return counts;
    }, {});

    addGroups();
    window.chatModule.trimChatHistoryDOM();
    const oldestTrim = {
      count: box.children.length,
      counts: groupCounts(),
      hasOldest: !!box.querySelector('[data-dom-trim-group="group-0"]'),
      hasNewest: !!box.querySelector('[data-dom-trim-group="group-89"]'),
    };

    addGroups();
    window.chatModule.trimChatHistoryDOM({ from: 'end' });
    const newestTrim = {
      count: box.children.length,
      counts: groupCounts(),
      hasOldest: !!box.querySelector('[data-dom-trim-group="group-0"]'),
      hasNewest: !!box.querySelector('[data-dom-trim-group="group-89"]'),
    };

    box.innerHTML = '';
    for (let i = 0; i < 151; i++) {
      const node = document.createElement('div');
      node.className = i < 2 ? 'msg streaming' : 'msg';
      node.dataset.domTrimGroup = i < 2 ? 'live-turn' : `done-${i}`;
      box.appendChild(node);
    }
    window.chatModule.trimChatHistoryDOM();
    const protectedLive = {
      count: box.children.length,
      liveCount: box.querySelectorAll('[data-dom-trim-group="live-turn"]').length,
    };

    window.sessionModule.setCurrentSessionId('foreground-session');
    const originalDetach = window.chatModule.detachCurrentStream;
    window.chatModule.detachCurrentStream = () => false;
    const switchResult = await window.sessionModule.selectSession('blocked-session', { showLoading: false });
    const afterBlockedSwitch = window.sessionModule.getCurrentSessionId();
    const newChatResult = window.sessionModule.createDirectChat('http://example.test/v1', 'test-model', 'test-endpoint');
    const afterBlockedNewChat = window.sessionModule.getCurrentSessionId();
    window.chatModule.detachCurrentStream = originalDetach;

    return {
      oldestTrim,
      newestTrim,
      protectedLive,
      switchResult,
      afterBlockedSwitch,
      newChatResult,
      afterBlockedNewChat,
    };
  });

  assert(results.oldestTrim.count <= 150, 'oldest-edge trim did not enforce the bound');
  assert(!results.oldestTrim.hasOldest && results.oldestTrim.hasNewest, 'normal trim removed the wrong edge');
  assert(Object.values(results.oldestTrim.counts).every(count => count === 2), 'normal trim split a rendered group');
  assert(results.newestTrim.count <= 150, 'newest-edge trim did not enforce the bound');
  assert(results.newestTrim.hasOldest && !results.newestTrim.hasNewest, 'back-page trim removed the wrong edge');
  assert(Object.values(results.newestTrim.counts).every(count => count === 2), 'back-page trim split a rendered group');
  assert(results.protectedLive.count <= 150 && results.protectedLive.liveCount === 2, 'protected live group was removed or bound was not restored');
  assert(results.switchResult === false && results.afterBlockedSwitch === 'foreground-session', 'blocked session switch mutated foreground state');
  assert(results.newChatResult === false && results.afterBlockedNewChat === 'foreground-session', 'blocked new-chat creation mutated foreground state');

  process.stdout.write(JSON.stringify(results, null, 2) + '\n');
  await browser.close();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
