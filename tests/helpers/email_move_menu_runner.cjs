// Run the shipped menu handlers with small DOM/transport collaborators.
// The native-app check is separate; this runner does not claim browser rendering.
const vm = require('node:vm');
const fs = require('node:fs');
const job = JSON.parse(fs.readFileSync(0, 'utf8'));
const unhandled = [];
process.on('unhandledRejection', error => unhandled.push(String(error)));

class Element {
  constructor() {
    this.children = [];
    this.handlers = {};
    this.style = {};
    this.classList = {add() {}, remove() {}, contains() { return false; }};
  }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  getBoundingClientRect() { return {bottom: 100, right: 400}; }
  contains() { return false; }
  remove() {}
  closest() { return this; }
}

function text(element) {
  return [element.textContent || '', ...element.children.map(text)].join('').trim();
}

function response() {
  return {
    ok: job.mode !== 'http-error', status: job.mode === 'http-error' ? 503 : 200,
    async json() {
      if (job.mode === 'invalid-json') throw new SyntaxError('Invalid JSON');
      if (job.mode === 'missing-success') return {};
      return {success: job.mode === 'success', error: 'Synthetic provider rejection'};
    },
  };
}

(async () => {
  const body = new Element();
  const original = {uid: '42', subject: 'Original', is_read: true};
  const calls = {requests: [], errors: [], animations: [], renders: 0, cache_writes: 0};
  const state = {_libEmails: [original], _libFolder: 'INBOX',
    _selectedUids: new Set(), _libSelectMode: false};
  let release;
  const context = vm.createContext({
    state, window: {innerWidth: 1440},
    document: {body, createElement: () => new Element(), querySelector: () => null,
      querySelectorAll: () => [], getElementById: () => null},
    console: {error() {}}, API_BASE: 'http://email.test', SELECT_MENU_ICON: '',
    topPortalZ: () => 1, bindMenuDismiss: () => () => {}, dismissOrRemove() {},
    actionMenuRank: () => 0, orderActionMenuItems: actions => actions,
    _fitEmailDropdown() {}, _acct: () => '&account_id=owned-account',
    _findSiblingEmailCard: () => null,
    _animateEmailCardRemoval: async uids => calls.animations.push([...uids]),
    _renderGrid: () => calls.renders++, _libCacheWriteBack: () => calls.cache_writes++,
    showEmailMoveError: message => calls.errors.push(message), showToast: message => calls.errors.push(message),
    fetch: async (url, options) => {
      calls.requests.push({url, method: options.method});
      if (job.mode === 'network-error') throw new Error('Synthetic disconnect');
      if (job.mode === 'pending') return new Promise(resolve => { release = resolve; });
      return response();
    },
  });
  vm.runInContext(job.source.replace(/^export /gm, ''), context);
  const card = new Element(), reader = new Element(), anchor = new Element();
  context._showReaderMoreMenu(original, card, reader, anchor, {});
  const dropdown = body.children[0];
  const label = 'Move to Spam';
  const action = dropdown.children.find(item => item.handlers.click && text(item) === label);
  if (!action) throw new Error('Menu action not found: '+label);
  action.handlers.click({stopPropagation() {}});
  const tick = () => new Promise(resolve => setImmediate(resolve));
  await tick();
  const whilePending = {uids: state._libEmails.map(e => e.uid), animations: calls.animations.length,
    cache_writes: calls.cache_writes, error_count: calls.errors.length};
  if (job.mode === 'pending') { release(response()); await tick(); }
  await tick();
  process.stdout.write(JSON.stringify({uids: state._libEmails.map(e => e.uid),
    while_pending: whilePending, calls, unhandled}));
})().catch(error => { console.error(error); process.exitCode = 1; });
