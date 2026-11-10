// First-run setup page. The server reports a state; this page shows the matching view.

const STEPS = [
  ['check_computer', 'Checking your computer'],
  ['find_agent', 'Finding a compatible agent'],
  ['install_components', 'Installing required components'],
  ['connect', 'Connecting everything'],
  ['test', 'Testing your setup'],
];
const STATUS_TEXT = { pending: 'Waiting', running: 'In progress', done: 'Done', failed: 'Problem' };
const STATUS_ICON = { pending: '○', running: '◐', done: '✓', failed: '✗' };
const VIEWS = ['loading', 'welcome', 'progress', 'consent', 'ready', 'problem'];

const $ = (id) => document.getElementById(id);
const live = $('live');
let currentView = '';
let pollTimer = null;
let busy = false;
let activeStepId = '';
let shownConsent = {};  // the question on screen; sent back with the answer

async function request(method, path, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    location.assign('/login');
    const err = new Error('unauthorized');
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    const err = new Error('HTTP ' + res.status);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function fmtBytes(n) {
  if (typeof n !== 'number' || !isFinite(n) || n < 0) return 'Unknown';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + ' GB';
  return Math.round(n / 1e6) + ' MB';
}

function showView(name) {
  if (name === currentView) return;
  currentView = name;
  for (const v of VIEWS) $('view-' + v).hidden = v !== name;
  $('view-' + name).querySelector('h1').focus();
}

function stopPoll() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

function schedulePoll() {
  stopPoll();
  pollTimer = setTimeout(poll, 1000);
}

function buildSteps() {
  const list = $('steps');
  for (const [id, label] of STEPS) {
    const li = document.createElement('li');
    li.dataset.step = id;
    li.dataset.status = 'pending';
    const icon = document.createElement('span');
    icon.className = 'step-icon';
    icon.setAttribute('aria-hidden', 'true');
    const name = document.createElement('span');
    name.className = 'step-name';
    name.textContent = label;
    const status = document.createElement('span');
    status.className = 'step-status';
    const detail = document.createElement('span');
    detail.className = 'step-detail';
    li.append(icon, name, status, detail);
    list.append(li);
  }
}

function renderProgress(s) {
  const steps = s.steps || [];
  let active = null;
  for (const step of steps) {
    const li = document.querySelector('#steps li[data-step="' + step.id + '"]');
    if (!li) continue;
    const status = STATUS_TEXT[step.status] ? step.status : 'pending';
    li.dataset.status = status;
    li.querySelector('.step-icon').textContent = STATUS_ICON[status];
    li.querySelector('.step-status').textContent = STATUS_TEXT[status];
    if (status === 'running') active = step;
  }
  for (const li of document.querySelectorAll('#steps li')) {
    const isActive = active && li.dataset.step === active.id;
    li.querySelector('.step-detail').textContent = isActive ? (active.detail || '') : '';
    if (isActive) li.setAttribute('aria-current', 'step');
    else li.removeAttribute('aria-current');
  }
  if (active && active.id !== activeStepId) {
    activeStepId = active.id;
    live.textContent = STEPS.find(([id]) => id === active.id)[1];
  }

  const progress = s.progress || {};
  const total = progress.total_bytes || 0;
  $('progress-wrap').hidden = !(total > 0);
  if (total > 0) {
    const done = Math.min(progress.done_bytes || 0, total);
    $('progress-bar').max = total;
    $('progress-bar').value = done;
    $('progress-label').textContent = fmtBytes(done) + ' of ' + fmtBytes(total);
  }
}

function renderSource(el, url) {
  el.textContent = '';
  let host = '';
  try { host = new URL(url).host; } catch (_) { /* not a URL: show as text */ }
  if (!host) {
    el.textContent = url || 'Unknown';
    return;
  }
  const at = url.indexOf(host);
  const strong = document.createElement('strong');
  strong.textContent = host;
  el.append(url.slice(0, at), strong, url.slice(at + host.length));
}

function showConsent(s) {
  const c = s.consent;
  shownConsent = { kind: c.kind, name: c.name };
  const copy = c.kind === 'add_model_copy';
  $('consent-intro').textContent = copy
    ? 'Odysseus would like to add a copy of a model you already have. The copy has a longer memory, which agent tasks need. Your model is not changed and nothing is downloaded.'
    : (c.kind === 'download_model'
      ? 'Odysseus needs to download its brain file.'
      : 'Odysseus needs a free helper program to think on this computer.')
      + ' Setup may also add a copy of the brain file with a longer memory. The copy needs no extra download.';
  $('btn-consent-yes').textContent = copy ? 'Yes, add the copy' : 'Yes, download and install';
  $('consent-name').textContent = c.name || 'Unknown';
  $('consent-size').textContent = c.size_bytes > 0 ? fmtBytes(c.size_bytes) : (copy ? 'No download' : 'Unknown');
  $('consent-cost').textContent = c.cost || 'Unknown';
  $('consent-license').textContent = c.license || 'Unknown';
  renderSource($('consent-source'), c.source_url || '');
  showView('consent');
}

function showProblem(message, hint) {
  $('problem-message').textContent = message || 'Setup could not finish.';
  $('problem-hint').textContent = hint || '';
  $('problem-hint').hidden = !hint;
  showView('problem');
}

// Maps a server state onto a view. Used for every status response.
function render(s) {
  stopPoll();
  if (!s) return;
  if (s.state === 'running') {
    showView('progress');
    renderProgress(s);
    schedulePoll();
  } else if (s.state === 'needs_consent' && s.consent) {
    showConsent(s);
  } else if (s.state === 'failed') {
    const err = s.error || {};
    showProblem(err.message, err.hint);
  } else if (s.state === 'done' || s.needed === false) {
    const note = (s.result && s.result.note) || '';
    $('ready-note').textContent = note;
    $('ready-note').hidden = !note;
    live.textContent = note;
    showView('ready');
  } else {
    showView('welcome');
  }
}

async function poll() {
  try {
    render(await request('GET', '/api/setup/status'));
  } catch (err) {
    if (err.status !== 401) schedulePoll();
  }
}

// Runs one API action at a time and turns failures into the problem view.
async function act(fn) {
  if (busy) return;
  busy = true;
  setButtonsDisabled(true);
  try {
    render(await fn());
  } catch (err) {
    if (err.status !== 401) showProblem('Setup could not be reached.', 'Check that Odysseus is still running, then try again.');
  } finally {
    busy = false;
    setButtonsDisabled(false);
  }
}

function setButtonsDisabled(disabled) {
  for (const btn of document.querySelectorAll('button')) btn.disabled = disabled;
}

function startSetup() {
  return request('POST', '/api/setup/start');
}

function answerConsent(approve) {
  return () => request('POST', '/api/setup/consent', { approve, ...shownConsent });
}

function wire() {
  buildSteps();
  $('btn-start').addEventListener('click', () => act(startSetup));
  $('btn-retry').addEventListener('click', () => act(startSetup));
  $('btn-repair').addEventListener('click', () => act(startSetup));
  $('btn-consent-yes').addEventListener('click', () => act(answerConsent(true)));
  $('btn-consent-no').addEventListener('click', () => act(answerConsent(false)));
  $('link-manual').addEventListener('click', (e) => {
    e.preventDefault();
    try { sessionStorage.setItem('setupSkipped', '1'); } catch (_) { /* storage unavailable */ }
    location.assign('/');
  });
}

async function init() {
  wire();
  showView('loading');
  try {
    render(await request('GET', '/api/setup/status'));
  } catch (err) {
    if (err.status === 401) return;
    if (err.status === 403) showProblem('Only the administrator can set up Odysseus.', 'Sign in as the administrator, then open this page again.');
    else showProblem('Setup could not be checked.', 'Refresh this page to try again.');
  }
}

init();
