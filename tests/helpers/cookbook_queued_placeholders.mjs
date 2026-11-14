import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const store = new Map([['cookbook-tasks', '[]']]);
const calls = [];
let reconnect = false;
let element;
const modal = {
  classList: { contains: () => false },
  querySelector: () => ({ dataset: { backend: 'Running' } }),
};
const jsonResponse = body => ({ ok: true, json: async () => body });
const context = vm.createContext({
  console, URL, AbortController, Date, Math, JSON, Set, Map,
  CSS: { escape: value => value },
  navigator: {},
  window: { cookbookModule: { isVisible: () => false } },
  document: {
    visibilityState: 'visible',
    addEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: id => reconnect && id === 'cookbook-modal' ? modal : null,
  },
  localStorage: {
    getItem: key => store.get(key) || null,
    setItem: (key, value) => store.set(key, value),
    removeItem: key => store.delete(key),
  },
  // Advance short UI/reconnect delays; request deadlines never fire here.
  setTimeout(callback, milliseconds) {
    if (milliseconds <= 10000) queueMicrotask(callback);
    return 1;
  },
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  async fetch(url, init = {}) {
    if (url === '/api/shell/exec') {
      calls.push(JSON.parse(init.body).command);
      element?._abort?.abort();
      return jsonResponse({ exit_code: 1, output: '' });
    }
    if (url === '/api/cookbook/tasks/status') {
      return jsonResponse({ tasks: input.live || [] });
    }
    return jsonResponse({ tasks: [] });
  },
});

// Load the whole production module, exposing private monitors only for tests.
const source = fs.readFileSync('static/js/cookbookRunning.js', 'utf8')
  + '\nexport { _pollBackgroundStatus, _reconnectTask };\n';
const module = new vm.SourceTextModule(source, { context });
await module.link(async specifier => {
  const ports = specifier.includes('ui.js')
    ? { default: { showToast() {}, showError() {} } }
    : specifier.includes('diagnosis')
      ? { _diagnose() {}, _showDiagnosis() {}, _clearDiagnosis() {} }
      : specifier.includes('escMenuStack')
        ? { registerMenuDismiss() {} }
        : specifier.includes('ProgressSignal')
          ? { computeProgressSignal: () => '' }
          : specifier.includes('Ports')
            ? { portOf: () => null, nextFreePort: () => 8000 }
            : { topPortalZ: () => 1 };
  return new vm.SyntheticModule(Object.keys(ports), function () {
    for (const [name, value] of Object.entries(ports)) this.setExport(name, value);
  }, { context });
});
await module.evaluate();
module.namespace.initRunning({
  _envState: { servers: [] },
  _isWindows: () => false,
  _getPlatform: () => '',
  _getPort: () => '',
  _sshPrefix: () => '',
  _loadPresets: () => [],
  _savePresets() {},
  _persistEnvState() {},
  _refreshDependencies() {},
  _serverByVal: () => null,
  _selectedServer: () => null,
  esc: value => String(value),
  modelLogo: () => '',
});
// Let the empty-state bootstrap finish before seeding the case's real tasks.
await new Promise(resolve => setImmediate(resolve));
store.set('cookbook-tasks', JSON.stringify(input.tasks || []));
if (input.kind === 'reconnect') {
  reconnect = true;
  const wrap = { classList: { contains: () => false } };
  element = {
    isConnected: true,
    querySelector: selector => selector === '.cookbook-output-wrap'
      ? wrap : selector === '.cookbook-output-pre' ? { textContent: '' } : null,
  };
  await module.namespace._reconnectTask(element, input.task);
} else {
  await module.namespace._pollBackgroundStatus();
}
console.log(JSON.stringify({
  tasks: JSON.parse(store.get('cookbook-tasks') || '[]'), calls,
}));
